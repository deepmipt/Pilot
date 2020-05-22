# Copyright 2017 Neural Networks and Deep Learning lab, MIPT
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import itertools
from logging import getLogger
from typing import Tuple, List, Any, Optional, Union

import re
import nltk

from deeppavlov.core.common.registry import register
from deeppavlov.core.models.component import Component
from deeppavlov.core.models.serializable import Serializable
from deeppavlov.core.common.file import read_json
from deeppavlov.models.kbqa.template_matcher import TemplateMatcher
from deeppavlov.models.kbqa.entity_linking import EntityLinker
from deeppavlov.models.kbqa.wiki_parser import WikiParser
from deeppavlov.models.kbqa.rel_ranking_infer import RelRankerInfer
from deeppavlov.models.kbqa.rel_ranking_bert_infer import RelRankerBertInfer
from deeppavlov.models.kbqa.utils import \
    extract_year, extract_number, asc_desc, make_combs, fill_query

log = getLogger(__name__)


@register('query_generator')
class QueryGenerator(Component, Serializable):
    """
        This class takes as input entity substrings, defines the template of the query and
        fills the slots of the template with candidate entities and relations.
    """

    def __init__(self, template_matcher: TemplateMatcher,
                 linker_entities: EntityLinker,
                 linker_types: EntityLinker,
                 wiki_parser: WikiParser,
                 rel_ranker: Union[RelRankerInfer, RelRankerBertInfer],
                 load_path: str,
                 rank_rels_filename_1: str,
                 rank_rels_filename_2: str,
                 sparql_queries_filename: str,
                 entities_to_leave: int = 5,
                 rels_to_leave: int = 10,
                 rels_to_leave_2hop: int = 7,
                 return_answers: bool = False, **kwargs) -> None:
        """

        Args:
            template_matcher: component deeppavlov.models.kbqa.template_matcher
            linker: component deeppavlov.models.kbqa.entity_linking
            wiki_parser: component deeppavlov.models.kbqa.wiki_parser
            rel_ranker: component deeppavlov.models.kbqa.rel_ranking_infer
            load_path: path to folder with wikidata files
            rank_rels_filename_1: file with list of rels for first rels in questions with ranking 
            rank_rels_filename_2: file with list of rels for second rels in questions with ranking
            entities_to_leave: how many entities to leave after entity linking
            rels_to_leave: how many relations to leave after relation ranking
            rels_to_leave_2hop: how many relations to leave in 2-hop questions
            sparql_queries_filename: file with a dict of sparql queries
            return_answers: whether to return answers or candidate answers
            **kwargs:
        """
        super().__init__(save_path=None, load_path=load_path)
        self.template_matcher = template_matcher
        self.linker_entities = linker_entities
        self.linker_types = linker_types
        self.wiki_parser = wiki_parser
        self.rel_ranker = rel_ranker
        self.rank_rels_filename_1 = rank_rels_filename_1
        self.rank_rels_filename_2 = rank_rels_filename_2
        self.entities_to_leave = entities_to_leave
        self.rels_to_leave = rels_to_leave
        self.rels_to_leave_2hop = rels_to_leave_2hop
        self.sparql_queries_filename = sparql_queries_filename
        self.return_answers = return_answers

        self.load()

    def load(self) -> None:
        with open(self.load_path / self.rank_rels_filename_1, 'r') as fl1:
            lines = fl1.readlines()
            self.rank_list_0 = [line.split('\t')[0] for line in lines]

        with open(self.load_path / self.rank_rels_filename_2, 'r') as fl2:
            lines = fl2.readlines()
            self.rank_list_1 = [line.split('\t')[0] for line in lines]

        self.template_queries = read_json(self.load_path / self.sparql_queries_filename)

    def save(self) -> None:
        pass

    def __call__(self, question_batch: List[str],
                 template_type_batch: List[str],
                 entities_from_ner_batch: List[List[str]],
                 types_from_ner_batch: List[List[str]]) -> List[Tuple[str]]:

        candidate_outputs_batch = []
        for question, template_type, entities_from_ner, types_from_ner in \
            zip(question_batch, template_type_batch, entities_from_ner_batch, types_from_ner_batch):

            candidate_outputs = []
            self.template_num = template_type

            replace_tokens = [(' - ', '-'), (' .', ''), ('{', ''), ('}', ''), ('  ', ' '), ('"', "'"), ('(', ''),
                              (')', ''), ('–', '-')]
            for old, new in replace_tokens:
                question = question.replace(old, new)

            entities_from_template, types_from_template, rels_from_template, rel_dirs_from_template, \
                query_type_template = self.template_matcher(question)
            self.template_num = query_type_template

            log.debug(f"question: {question}\n")
            log.debug(f"template_type {self.template_num}")

            if entities_from_template or types_from_template:
                entity_ids = self.get_entity_ids(entities_from_template, "entities")
                type_ids = self.get_entity_ids(types_from_template, "types")
                log.debug(f"entities_from_template {entities_from_template}")
                log.debug(f"types_from_template {types_from_template}")
                log.debug(f"rels_from_template {rels_from_template}")
                log.debug(f"entity_ids {entity_ids}")
                log.debug(f"type_ids {type_ids}")

                candidate_outputs = self.find_candidate_answers(question, entity_ids, type_ids, rels_from_template, rel_dirs_from_template)

            if not candidate_outputs and entities_from_ner:
                log.debug(f"(__call__)entities_from_ner: {entities_from_ner}")
                log.debug(f"(__call__)types_from_ner: {types_from_ner}")
                entity_ids = self.get_entity_ids(entities_from_ner, "entities")
                type_ids = self.get_entity_ids(types_from_ner, "types")
                log.debug(f"(__call__)entity_ids: {entity_ids}")
                log.debug(f"(__call__)type_ids: {type_ids}")
                self.template_num = template_type[0]
                log.debug(f"(__call__)self.template_num: {self.template_num}")
                candidate_outputs = self.find_candidate_answers(question, entity_ids, type_ids)
            candidate_outputs_batch.append(candidate_outputs)
        if self.return_answers:
            answers = self.rel_ranker(question_batch, candidate_outputs_batch)
            log.debug(f"(__call__)answers: {answers}")
            return answers
        else:
            log.debug(f"(__call__)candidate_outputs_batch: {candidate_outputs_batch}")
            return candidate_outputs_batch

    def get_entity_ids(self, entities: List[str], what_to_link: str) -> List[List[str]]:
        entity_ids = []
        for entity in entities:
            if what_to_link == "entities":
                entity_id, confidences = self.linker_entities(entity)
            if what_to_link == "types":
                entity_id, confidences = self.linker_types(entity)
            entity_ids.append(entity_id[:15])
        return entity_ids


    def find_candidate_answers(self, question: str,
                               entity_ids: List[List[str]],
                               type_ids: List[List[str]],
                               rels_from_template: Optional[List[Tuple[str]]] = None,
                               rel_dirs_from_template: Optional[List[str]] = None) -> List[Tuple[str]]:
        candidate_outputs = []
        log.debug(f"(find_candidate_answers)self.template_num: {self.template_num}")

        templates = self.template_queries[self.template_num]
        templates = [template for template in templates if template["entities_and_types_num"] == [len(entity_ids), len(type_ids)]]
        if rels_from_template is not None:
            query_template = {}
            for template in templates:
                if template["rel_dirs"] == rel_dirs_from_template:
                    query_template = template
            if query_template:
                candidate_outputs = self.query_parser(question, query_template, entity_ids, type_ids, rels_from_template)
        else:
            for template in templates:
                candidate_outputs = self.query_parser(question, template, entity_ids, type_ids, rels_from_template)
                if candidate_outputs:
                    return candidate_outputs
            
            if not candidate_outputs:
                alternative_templates = templates[0]["alternative_templates"]
                for template in alternative_templates:
                    candidate_outputs = self.query_parser(question, template, entity_ids, type_ids, rels_from_template)
                    return candidate_outputs

        log.debug("candidate_rels_and_answers:\n" + '\n'.join([str(output) for output in candidate_outputs]))

        return candidate_outputs
    
    def query_parser(self, question, query_info, entity_ids, type_ids, rels_from_template = None):
        # TODO: lowercase query
        candidate_outputs = []
        question_tokens = nltk.word_tokenize(question)

        query = query_info["query_template"]
        rels_for_search = query_info["rank_rels"]
        query_seq_num = query_info["query_sequence"]
        return_if_found = query_info["return_if_found"]
        log.debug(f"(query_parser)quer: {query}, {rels_for_search}, {query_seq_num}, {return_if_found}")
        query_triplets = query[query.find('{')+1:query.find('}')].strip(' ').split(' . ') #TODO: use re for {}
        log.debug(f"(query_parser)query_triplets: {query_triplets}")
        query_triplets = [triplet.split(' ')[:3] for triplet in query_triplets]
        query_sequence = []
        for i in range(1, max(query_seq_num)+1):
            query_sequence.append([triplet for num, triplet in zip(query_seq_num, query_triplets) if num == i]) #TODO: dict instead of zip
        log.debug(f"(query_parser)query_sequence: {query_sequence}")
        rel_directions = [("forw" if triplet[2].startswith('?') else "backw", search_or_not) 
            for search_or_not, triplet in zip(rels_for_search, query_triplets) if search_or_not]
        log.debug(f"(query_parser)rel_directions: {rel_directions}")
        entity_combs = make_combs(entity_ids, permut=True) #TODO: rename function
        log.debug(f"(query_parser)entity_combs: {entity_combs[:3]}")
        type_combs = make_combs(type_ids, permut=False)
        log.debug(f"(query_parser)type_combs: {type_combs[:3]}")
        rels = []
        '''self.templates["when was eee discovered?"] = ["7", ("P571", "forw")]'''
        if rels_from_template is not None:
            rels = rels_from_template
        else:
            rels = [self.find_top_rels(question, entity_ids, d) for d in rel_directions]

        log.debug(f"(query_parser)rels: {rels}")
        '''
        ("SELECT ?obj WHERE { wd:E1 p:R1 ?s . ?s ps:R1 ?obj . ?s ?p ?x filter(contains(?x, YEAR)&&contains(?p, 'qualifier')) }",
               (1, 0, 0), (1, 2, 3), True) 
        ("SELECT ?ent WHERE { ?ent wdt:P31 wd:T1 . ?ent wdt:R1 ?obj . ?ent wdt:R2 wd:E1 } ORDER BY ASC(?obj) LIMIT 5" '''
        rels_from_query = [triplet[1] for triplet in query_triplets if triplet[1].startswith('?')]
        answer_ent = re.findall("SELECT [\(]?([\S]+) ", query)
        order_from_query = re.findall("ORDER BY ([ASC|DESC])\((.*)\)", query) # TODO: refactor regexp
        ascending = asc_desc(question)
        log.debug(f"question, ascending: {question}, {ascending}")
        if not ascending: # descending
            order_from_query = [("DESC", elem[1]) for elem in order_from_query]
        log.debug(f"(query_parser)answer_ent: {answer_ent}, order_from_query: {order_from_query}")
        filter_from_query = re.findall("contains\((\?\w), (.+?)\)", query)
        log.debug("(parser_query)filter_from_query: {filter_from_query}") #TODO: make more compact

        year = extract_year(question_tokens, question)
        number = extract_number(question_tokens, question)
        if year:
            filter_from_query = [elem[1].replace("YEAR", year) for elem in filter_from_query]
        else:
            filter_from_query = [elem for elem in filter_from_query if elem[1] != "YEAR"]
        if number:
             filter_from_query = [elem[1].replace("NUMBER", number) for elem in filter_from_query]
        else:
            filter_from_query = [elem for elem in filter_from_query if elem[1] != "NUMBER"]
        log.debug(f"(query_parser)filter_from_query: {filter_from_query}")
        rel_combs = make_combs(rels, permut=False)
        import datetime
        start_time = datetime.datetime.now()
        for combs in itertools.product(entity_combs, type_combs, rel_combs):
            query_hdt_seq = [
                fill_query(query_hdt_elem, combs[0], combs[1], combs[2]) for query_hdt_elem in query_sequence]
            candidate_output = self.wiki_parser(
                rels_from_query + answer_ent, query_hdt_seq, filter_from_query, order_from_query)
            candidate_outputs += [combs[2][:-1] + output for output in candidate_output]
            if return_if_found and candidate_output:
                return candidate_outputs
        log.debug(f"(query_parser)loop time: {datetime.datetime.now() - start_time}")
        log.debug(f"(query_parser)final outputs: {candidate_outputs}")

        return candidate_outputs

    def find_top_rels(self, question, entity_ids, triplet_direction):
        ex_rels = []
        if triplet_direction[1] == 1: #TODO: replace numbers with strings, source instead with triplet_direction
            for entity_id in entity_ids:
                for entity in entity_id[:self.entities_to_leave]:
                    ex_rels += self.wiki_parser.find_rels(entity, triplet_direction[0])
            ex_rels = list(set(ex_rels))
            ex_rels = [rel.split('/')[-1] for rel in ex_rels]
        if triplet_direction[1] == 2:
            ex_rels = self.rank_list_0
        if triplet_direction[1] == 3: # elif 
            ex_rels = self.rank_list_1
        scores = self.rel_ranker.rank_rels(question, ex_rels)
        top_rels = [score[0] for score in scores]
        return top_rels
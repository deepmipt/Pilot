import shutil
from collections import defaultdict
from pathlib import Path

from deeppavlov.core.common.registry import register
from deeppavlov.core.data.utils import is_done, mark_done
from deeppavlov.core.common import paths
from deeppavlov.core.common.file import load_pickle, save_pickle


@register('static_dictionary')
class StaticDictionary:
    dict_name = None

    @staticmethod
    def _get_source(*args, **kwargs):
        raw_path = args[2] if len(args) > 2 else kwargs.get('raw_dictionary_path', None)
        if not raw_path:
            raise RuntimeError('raw_path for StaticDictionary is not set')
        with open(raw_path, newline='') as f:
            data = [line.strip().split('\t')[0] for line in f]
        return data

    @staticmethod
    def _normalize(word):
        return '⟬{}⟭'.format(word.strip().lower().replace('ё', 'е'))

    def __init__(self, data_dir=None, *args, **kwargs):
        if data_dir is None:
            data_dir = paths.USR_PATH
        data_dir = Path(data_dir)
        if self.dict_name is None:
            self.dict_name = args[0] if args else kwargs.get('dictionary_name', 'dictionary')

        data_dir = data_dir / self.dict_name

        alphabet_path = data_dir / 'alphabet.pkl'
        words_path = data_dir / 'words.pkl'
        words_trie_path = data_dir / 'words_trie.pkl'

        if not is_done(data_dir):
            print('Trying to build a dictionary in {}'.format(data_dir))
            if data_dir.is_dir():
                shutil.rmtree(data_dir)
            data_dir.mkdir(mode=0o755, parents=True)

            words = self._get_source(data_dir, *args, **kwargs)
            words = {self._normalize(word) for word in words}

            alphabet = {c for w in words for c in w}
            alphabet.remove('⟬')
            alphabet.remove('⟭')

            save_pickle(alphabet, alphabet_path)
            save_pickle(words, words_path)

            words_trie = defaultdict(set)
            for word in words:
                for i in range(len(word)):
                    words_trie[word[:i]].add(word[:i+1])
                words_trie[word] = set()
            words_trie = {k: sorted(v) for k, v in words_trie.items()}

            save_pickle(words_trie, words_trie_path)

            mark_done(data_dir)
            print('built')
        else:
            print('Loading a dictionary from {}'.format(data_dir))

        self.alphabet = load_pickle(alphabet_path)
        self.words_set = load_pickle(words_path)
        self.words_trie = load_pickle(words_trie_path)

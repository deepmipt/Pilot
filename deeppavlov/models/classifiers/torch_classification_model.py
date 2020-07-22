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

from typing import List, Union, Optional
import logging

import torch
import torch.nn as nn

import numpy as np
from deeppavlov.core.common.errors import ConfigError
from deeppavlov.core.models.torch_model import TorchModel
from deeppavlov.core.common.registry import register
from .torch_nets import ShallowAndWideCnn

log = logging.getLogger(__name__)


@register('torch_text_classification_model')
class TorchTextClassificationModel(TorchModel):
    """Class implements torch model for classification of texts.
    Input can either be embedded tokenized texts OR indices of words in the vocabulary.
    Number of tokens is not fixed while the samples in batch should be padded to the same (e.g. longest) lengths.

    Args:
        n_classes: number of classes
        model_name: name of `TorchTextClassificationModel` methods which initializes model architecture
        embedding_size: size of vector representation of words
        multi_label: is multi-label classification (if so, `sigmoid` activation will be used, otherwise, softmax)
        criterion: criterion name from `torch.nn`
        optimizer: optimizer name from `torch.optim`
        optimizer_parameters: dictionary with optimizer's parameters,
                              e.g. {'lr': 0.1, 'weight_decay': 0.001, 'momentum': 0.9}
        lr_scheduler: string name of scheduler class from `torch.optim.lr_scheduler`
        lr_scheduler_parameters: parameters for scheduler
        embedded_tokens: True, if input contains embedded tokenized texts;
                         False, if input containes indices of words in the vocabulary
        vocab_size: vocabulary size in case of `embedded_tokens=False`
        lr_decay_every_n_epochs: how often to decay lr
        lr_decay_patience: validation patience to decrease lr if target metrics do not become better

    Attributes:
        opt: dictionary with all model parameters
        n_classes: number of considered classes
        model: torch model itself
        epochs_done: number of epochs that were done
        optimizer: torch optimizer instance
        criterion: torch criterion instance
    """

    def __init__(self, n_classes: int, model_name: str, embedding_size: int = None, multi_label: bool = False,
                 criterion: str = "CrossEntropyLoss", optimizer: str = "Adam", optimizer_parameters: dict = {"lr": 0.1},
                 lr_scheduler: str = None, lr_scheduler_parameters: Optional[dict] = {},
                 embedded_tokens=True, vocab_size=None, lr_decay_every_n_epochs: Optional[int] = None,
                 lr_decay_patience: Optional[int] = None, **kwargs):
        if n_classes == 0:
            raise ConfigError("Please, provide vocabulary with considered classes or number of classes.")

        full_kwargs = {
            "embedding_size": embedding_size,
            "n_classes": n_classes,
            "model_name": model_name,
            "optimizer": optimizer,
            "criterion": criterion,
            "multi_label": multi_label,
            "optimizer_parameters": optimizer_parameters,
            "embedded_tokens": embedded_tokens,
            "vocab_size": vocab_size,
            "lr_decay_every_n_epochs": lr_decay_every_n_epochs,
            "lr_decay_patience": lr_decay_patience,
            "lr_scheduler": lr_scheduler,
            "lr_scheduler_parameters": lr_scheduler_parameters,
            **kwargs,
        }
        super().__init__(**full_kwargs)

    def __call__(self, data: List[List[np.ndarray]], *args) -> List[List[float]]:
        """Infer on the given data.

        Args:
            data: list of tokenized text samples
            *args: additional arguments

        Returns:
            for each sentence:
                vector of probabilities to belong with each class
                or list of labels sentence belongs with
        """
        preds = np.array(self.infer_on_batch(data), dtype="float64").tolist()
        return preds

    def process_event(self, event_name: str, data: dict):
        """
        Process event after epoch
        Args:
            event_name: whether event is send after epoch or batch.
                    Set of values: ``"after_epoch", "after_batch"``
            data: event data (dictionary)
        Returns:
            None
        """
        super().process_event(event_name, data)

        if event_name == "after_epoch" and self.opt.get("lr_decay_every_n_epochs", None) is not None:
            if self.epochs_done % self.opt["lr_decay_every_n_epochs"] == 0:
                log.info(f"----------Current LR is decreased in 10 times----------")
                for param_group in self.optimizer.param_groups:
                    param_group['lr'] = param_group['lr'] / 10
        if event_name == "after_validation" and 'impatience' in data and self.opt.get("lr_decay_patience", None):
            if data['impatience'] == self.opt["lr_decay_patience"]:
                log.info(f"----------Current LR is decreased in 10 times----------")
                for param_group in self.optimizer.param_groups:
                    param_group['lr'] = param_group['lr'] / 10

    def train_on_batch(self, texts: List[List[np.ndarray]], labels: list) -> Union[float, List[float]]:
        """Train the model on the given batch.

        Args:
            texts: vectorized texts
            labels: list of labels

        Returns:
            metrics values on the given batch
        """
        features, labels = np.array(texts), np.array(labels)

        inputs, labels = torch.from_numpy(features), torch.from_numpy(labels)
        inputs, labels = inputs.to(self.device), labels.to(self.device)
        # zero the parameter gradients
        self.optimizer.zero_grad()

        # forward + backward + optimize
        outputs = self.model(inputs)
        labels = labels.view(-1).long()
        loss = self.criterion(outputs, labels)
        loss.backward()
        self.optimizer.step()
        if self.lr_scheduler is not None:
            self.lr_scheduler.step()
        return loss.item()

    def infer_on_batch(self, texts: List[List[np.ndarray]],
                       labels: list = None) -> Union[float, List[float], np.ndarray]:
        """Infer the model on the given batch.

        Args:
            texts:
            labels: list of labels

        Returns:
            predictions, otherwise
        """
        with torch.no_grad():
            features = np.array(texts)
            inputs = torch.from_numpy(features)
            inputs = inputs.to(self.device)
            outputs = self.model(inputs)

        return outputs.cpu().detach().numpy()

    def cnn_model(self, kernel_sizes_cnn: List[int], filters_cnn: int, dense_size: int, dropout_rate: float = 0.0,
                  **kwargs) -> nn.Module:
        """Build un-compiled model of shallow-and-wide CNN.

        Args:
            kernel_sizes_cnn: list of kernel sizes of convolutions.
            filters_cnn: number of filters for convolutions.
            dense_size: number of units for dense layer.
            dropout_rate: dropout rate, after convolutions and between dense.
            kwargs: other parameters

        Returns:
            torch.models.Model: instance of torch Model
        """
        model = ShallowAndWideCnn(n_classes=self.opt["n_classes"], embedding_size=self.opt["embedding_size"],
                                  kernel_sizes_cnn=kernel_sizes_cnn, filters_cnn=filters_cnn,
                                  dense_size=dense_size, dropout_rate=dropout_rate,
                                  multi_label=self.opt["multi_label"], embedded_tokens=self.opt["embedded_tokens"],
                                  vocab_size=self.opt["vocab_size"])
        model.to(self.device)
        return model
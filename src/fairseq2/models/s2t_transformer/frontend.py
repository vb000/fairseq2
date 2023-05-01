# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import math
from typing import Optional, Tuple, final

import torch
from overrides import final as finaloverride
from torch import Tensor
from torch.nn import Dropout

from fairseq2.models.encoder_decoder import EncoderDecoderFrontend
from fairseq2.models.feature_extractor import FeatureExtractor
from fairseq2.nn.incremental_state import IncrementalStateBag
from fairseq2.nn.positional_embedding import PositionalEmbedding
from fairseq2.nn.projection import Linear, Projection
from fairseq2.nn.utils.mask import to_padding_mask


@final
class S2TTransformerFrontend(EncoderDecoderFrontend):
    """Represents a Transformer model front-end as described in Section 2.1 of
    :cite:t:`https://doi.org/10.48550/arxiv.1911.08460`."""

    feat_extractor: Optional[FeatureExtractor]
    scale: float
    pos_embed: Optional[PositionalEmbedding]
    proj: Optional[Projection]
    dropout: Optional[Dropout]

    def __init__(
        self,
        model_dim: int,
        feat_extractor: Optional[FeatureExtractor],
        pos_embed: Optional[PositionalEmbedding],
        apply_projection: bool = False,
        dropout_p: float = 0.1,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> None:
        """
        :param model_dim:
            The dimensionality of the model (i.e. inputs and outputs).
        :param feat_extractor:
            The feature extractor. If ``None``, it is assumed that features are
            extracted externally before being fed to the model.
        :param pos_embed:
            The positional embedding.
        :param apply_projection:
            If ``True``, applies projection to outputs before dropout as
            described in Section 2 of
            :cite:t:`https://doi.org/10.48550/arxiv.2005.08100`.
        :param dropout_p:
            The dropout probability on outputs.
        """
        super().__init__(model_dim)

        if feat_extractor is not None:
            if feat_extractor.embed_dim != model_dim:
                raise ValueError(
                    f"`embed_dim` of `feat_extractor` and `model_dim` must be equal, but are {feat_extractor.embed_dim} and {model_dim} instead."
                )

            self.feat_extractor = feat_extractor
        else:
            self.register_module("feat_extractor", None)

        self.scale = math.sqrt(model_dim)

        if pos_embed is not None:
            if pos_embed.embed_dim != model_dim:
                raise ValueError(
                    f"`embed_dim` of `pos_embed` and `model_dim` must be equal, but are {pos_embed.embed_dim} and {model_dim} instead."
                )

            self.pos_embed = pos_embed
        else:
            self.register_module("pos_embed", None)

        if apply_projection:
            self.proj = Linear(
                model_dim, model_dim, bias=True, device=device, dtype=dtype
            )
        else:
            self.register_module("proj", None)

        if dropout_p > 0.0:
            self.dropout = Dropout(dropout_p)
        else:
            self.register_module("dropout", None)

    @finaloverride
    def forward(
        self,
        seqs: Tensor,
        seq_lens: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag] = None,
    ) -> Tuple[Tensor, Optional[Tensor]]:
        if self.feat_extractor is not None:
            seqs, seq_lens = self.feat_extractor(seqs, seq_lens)

        padding_mask = to_padding_mask(seqs, seq_lens)

        seqs = seqs * self.scale

        if self.pos_embed is not None:
            seqs = self.pos_embed(seqs, padding_mask, state_bag)

        if self.proj is not None:
            seqs = self.proj(seqs)

        if self.dropout is not None:
            seqs = self.dropout(seqs)

        return seqs, padding_mask

    def extra_repr(self) -> str:
        """:meta private:"""
        return "no_scale=False" if self.scale != 1.0 else ""

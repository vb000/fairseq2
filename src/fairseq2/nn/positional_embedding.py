# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

import math
from abc import ABC, abstractmethod
from typing import Optional, cast, final

import torch
import torch.nn as nn
import torch.nn.functional as F
from overrides import final as finaloverride
from torch import Tensor
from torch.nn import Module
from torch.nn.parameter import Parameter

from fairseq2.nn.incremental_state import IncrementalStateBag


class PositionalEmbedding(Module, ABC):
    """Produces positional embeddings."""

    embed_dim: int
    max_seq_len: Optional[int]

    def __init__(self, embed_dim: int, max_seq_len: Optional[int]) -> None:
        """
        :param embed_dim:
            The dimensionality of positional embeddings.
        :param max_seq_len:
            The expected maximum sequence length.
        """
        super().__init__()

        self.embed_dim = embed_dim
        self.max_seq_len = max_seq_len

    def forward(
        self,
        seqs: Tensor,
        padding_mask: Optional[Tensor] = None,
        state_bag: Optional[IncrementalStateBag] = None,
    ) -> Tensor:
        """
        :param seqs:
            The sequences which will be encoded with positional information.
            *Shape:* :math:`(N,S,E)`, where :math:`N` is the batch size,
            :math:`S` is the sequence length, and :math:`E` is the positional
            embedding size.
        :param padding_mask:
            The float padding mask of ``seqs``. *Shape:* :math:`(N_{msk},S)`,
            where :math:`N_{msk}` is the batch size of the mask and :math:`S` is
            the sequence length. :math:`N` can be a multiple of :math:`N_{msk}`
            in which case the mask will be tiled before being applied.
        :param state_bag:
            The state bag to use during an incremental evaluation.

        :returns:
            ``seqs`` with positional information encoded. *Shape:* Same as
            ``seqs``.
        """
        if self.max_seq_len is not None:
            if (seq_len := seqs.size(1)) > self.max_seq_len:
                raise ValueError(
                    f"The input sequence length must be less than or equal to the maximum sequence length ({self.max_seq_len}), but is {seq_len} instead."
                )

        return self._do_forward(seqs, padding_mask, state_bag)

    @abstractmethod
    def _do_forward(
        self,
        seqs: Tensor,
        padding_mask: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag],
    ) -> Tensor:
        """
        :param seqs:
            The sequences which will be encoded with positional information.
            *Shape:* :math:`(N,S,E)`, where :math:`N` is the batch size,
            :math:`S` is the sequence length, and :math:`E` is the positional
            embedding size.
        :param padding_mask:
            The float padding mask of ``seqs``. *Shape:* :math:`(N_{msk},S)`,
            where :math:`N_{msk}` is the batch size of the mask and :math:`S` is
            the sequence length. If padding has to be applied, a derived class
            should use the :func:`~fairseq2.nn.utils.mask.apply_padding_mask`
            function.
        :param state_bag:
            The state bag to use during an incremental evaluation.

        :returns:
            ``seqs`` with positional information encoded. *Shape:* Same as
            ``seqs``.

        :meta public:
        """

    def extra_repr(self) -> str:
        """:meta private:"""
        s = f"embed_dim={self.embed_dim}"

        if self.max_seq_len is not None:
            s += f", max_seq_len={self.max_seq_len}"

        return s


@final
class SinusoidalPositionalEmbedding(PositionalEmbedding):
    """Produces sinusoidal positional embeddings.

    The positional embeddings are initialized as in tensor2tensor which differs
    slightly from the description in section 3.5 of
    :cite:t:`https://doi.org/10.48550/arxiv.1706.03762`. This means instead of

    .. math::
        PE_{(pos, 2i)}   = \\text{sin}(pos/10000^{2i/d_{model}})

        PE_{(pos, 2i+1)} = \\text{cos}(pos/10000^{2i/d_{model}})

    we use

    .. math::
        PE_{(pos, i)} = \\text{sin}(pos/10000^{i/d_{model}})\\;\\text{for}\\;i\\;    <\\frac{d_{model}}{2}

        PE_{(pos, i)} = \\text{cos}(pos/10000^{i/d_{model}})\\;\\text{for}\\;i\\;\\geq\\frac{d_{model}}{2}

    See `here <https://github.com/tensorflow/tensor2tensor/pull/177>`_ for more
    information.

    Usage:

    >>> import torch
    >>>
    >>> from fairseq2.nn.positional_embedding import SinusoidalPositionalEmbedding
    >>>
    >>> m = SinusoidalPositionalEmbedding(max_seq_len=16, embed_dim=4)
    >>>
    >>> seqs = torch.ones((3, 4))
    >>>
    >>> m(seqs)
    tensor([[ 1.0000e+00,  1.0000e+00,  2.0000e+00,  2.0000e+00],  # pos 0
            [ 9.4147e-01,  2.0000e-04,  6.4030e-01,  2.0000e+00],  # pos 1
            [ 1.0930e-02,  3.0000e-04, -5.1615e-01,  2.0000e+00]]) # pos 2
    """

    weight: Tensor

    def __init__(
        self,
        max_seq_len: int,
        embed_dim: int,
        legacy_pad_token_idx: Optional[int] = None,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> None:
        super().__init__(embed_dim, max_seq_len)

        # This is a legacy parameter that should only be set when the embeddings
        # must be compatible with fairseq.
        if legacy_pad_token_idx is None:
            self._sin_offset = 0
        else:
            self._sin_offset = 1 + legacy_pad_token_idx

        weight = torch.empty((max_seq_len, embed_dim), device=device, dtype=dtype)

        self.register_buffer("weight", weight, persistent=False)

        self.reset_buffers()

    def reset_buffers(self, skip_persistent: bool = False) -> None:
        """Reset the buffers of the module.

        :param skip_persistent:
            If ``True``, does not reset persistent buffers.
        """
        num_sin = self.embed_dim // 2

        # Zero pad if the embedding size is odd.
        if self.embed_dim > 2 * num_sin:
            self.weight[:, -1:] = 0

        l_half = self.weight[:, :num_sin]
        r_half = self.weight[:, num_sin:]

        device, dtype = self.weight.device, self.weight.dtype

        start = self._sin_offset

        max_seq_len = cast(int, self.max_seq_len)

        # This is identical to tensor2tensor's implementation.
        indices = torch.arange(start, start + max_seq_len, device=device, dtype=dtype)

        indices = indices.unsqueeze(1)

        sin = torch.arange(num_sin, device=device, dtype=dtype)

        sin = torch.exp(sin * -math.log(10000) / (num_sin - 1))

        sin = sin.unsqueeze(0)

        torch.matmul(indices, sin, out=l_half)

        r_half[:] = l_half[:]

        l_half.sin_()
        r_half.cos_()

    @finaloverride
    def _do_forward(
        self,
        seqs: Tensor,
        padding_mask: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag],
    ) -> Tensor:
        """:meta private:"""
        seq_len = seqs.size(1)

        if not self.training and state_bag is not None:
            start_step = state_bag.step
        else:
            start_step = 0

        return seqs + self.weight[start_step : start_step + seq_len]


@final
class LearnedPositionalEmbedding(PositionalEmbedding):
    """Learns positional embeddings.

    Usage:

    >>> import torch
    >>>
    >>> from fairseq2.nn.positional_embedding import LearnedPositionalEmbedding
    >>>
    >>> m = LearnedPositionalEmbedding(max_seq_len=16, embed_dim=4)
    >>>
    >>> seqs = torch.ones((3, 4))
    >>>
    >>> m(seqs)
    tensor([[ 1.1135,  0.5548,  0.4293,  2.0112],                               # pos 0
            [ 0.2364,  0.6009,  3.3865, -2.4810],                               # pos 1
            [-0.4746,  0.4544,  0.2761,  0.8828]], grad_fn=<SqueezeBackward1>)  # pos 2
    """

    weight: Parameter

    def __init__(
        self,
        max_seq_len: int,
        embed_dim: int,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> None:
        super().__init__(embed_dim, max_seq_len)

        self.weight = Parameter(
            torch.empty((max_seq_len, embed_dim), device=device, dtype=dtype)
        )

        self.reset_buffers()

    def reset_buffers(self, skip_persistent: bool = False) -> None:
        """Reset the buffers of the module.

        :param skip_persistent:
            If ``True``, does not reset persistent buffers.
        """
        nn.init.normal_(self.weight)

    @finaloverride
    def _do_forward(
        self,
        seqs: Tensor,
        padding_mask: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag],
    ) -> Tensor:
        """:meta private:"""
        seq_len = seqs.size(1)

        if not self.training and state_bag is not None:
            start_step = state_bag.step
        else:
            start_step = 0

        indices = torch.arange(
            start_step, start_step + seq_len, device=seqs.device, dtype=torch.int64
        )

        return seqs + F.embedding(indices, self.weight)


@final
class RotaryEmbedding(PositionalEmbedding):
    """Produces relative positional embeddings as described in
    :cite:t:`https://doi.org/10.48550/arxiv.2104.09864`."""

    cos_weight: Tensor
    sin_weight: Tensor

    def __init__(
        self,
        max_seq_len: int,
        embed_dim: int,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> None:
        if embed_dim % 2 != 0:
            raise ValueError(f"`embed_dim` must be even, but is {embed_dim} instead.")

        super().__init__(embed_dim, max_seq_len)

        cos = torch.empty((max_seq_len, embed_dim), device=device, dtype=dtype)
        sin = torch.empty((max_seq_len, embed_dim), device=device, dtype=dtype)

        self.register_buffer("cos_weight", cos, persistent=False)
        self.register_buffer("sin_weight", sin, persistent=False)

        self.reset_buffers()

    def reset_buffers(self, skip_persistent: bool = False) -> None:
        """Reset the buffers of the module.

        :param skip_persistent:
            If ``True``, does not reset persistent buffers.
        """
        device, dtype = self.sin_weight.device, self.sin_weight.dtype

        max_seq_len = cast(int, self.max_seq_len)

        indices = torch.arange(self.embed_dim // 2, device=device, dtype=dtype)

        indices = indices.unsqueeze(0)

        steps = torch.arange(max_seq_len, device=device, dtype=dtype)

        steps = steps.unsqueeze(1)

        embed = torch.matmul(steps, 10000 ** (-2.0 * indices / self.embed_dim))

        cos = torch.cos(embed)
        sin = torch.sin(embed)

        self.cos_weight[:] = torch.repeat_interleave(cos, 2, dim=-1)
        self.sin_weight[:] = torch.repeat_interleave(sin, 2, dim=-1)

    @finaloverride
    def _do_forward(
        self,
        seqs: Tensor,
        padding_mask: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag],
    ) -> Tensor:
        """:meta private:"""
        seq_len = seqs.size(1)

        if not self.training and state_bag is not None:
            start_step = state_bag.step
        else:
            start_step = 0

        seqs_swapped = self._swap_pairs(seqs)

        cos = self.cos_weight[start_step : start_step + seq_len] * seqs
        sin = self.sin_weight[start_step : start_step + seq_len] * seqs_swapped

        return cos + sin

    @staticmethod
    def _swap_pairs(seqs: Tensor) -> Tensor:
        x1 = seqs[..., 0::2]
        x2 = seqs[..., 1::2]

        return torch.stack((-x2, x1), dim=-1).reshape(seqs.shape)

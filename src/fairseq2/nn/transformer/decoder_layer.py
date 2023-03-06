# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from abc import ABC, abstractmethod
from typing import Optional, cast, final

import torch
import torch.nn as nn
import torch.nn.functional as F
from overrides import final as finaloverride
from torch import Tensor
from torch.nn import LayerNorm, Module
from torch.nn.parameter import Parameter

from fairseq2.nn.incremental_state import IncrementalStateBag
from fairseq2.nn.transformer.ffn import FeedForwardNetwork
from fairseq2.nn.transformer.multihead_attention import MultiheadAttention
from fairseq2.nn.transformer.norm_order import TransformerNormOrder


class TransformerDecoderLayer(Module, ABC):
    """Represents a Transformer decoder layer."""

    model_dim: int

    def __init__(self, model_dim: int) -> None:
        """
        :param model_dim:
            The dimensionality of the model (i.e. inputs and outputs).
        """
        super().__init__()

        self.model_dim = model_dim

    @abstractmethod
    def forward(
        self,
        x: Tensor,
        padding_mask: Optional[Tensor] = None,
        self_attn_mask: Optional[Tensor] = None,
        enc_out: Optional[Tensor] = None,
        enc_padding_mask: Optional[Tensor] = None,
        state_bag: Optional[IncrementalStateBag] = None,
    ) -> Tensor:
        """
        :param x:
            The input to decode. *Shape:* :math:`(N,S,M)`, or :math:`(S,M)` when
            unbatched, where :math:`N` is the batch size, :math:`S` is the
            sequence length, and :math:`M` is the model size.
        :param padding_mask:
            The boolean or float padding mask indicating which key positions to
            ignore for the purpose of self attention. *Shape:* :math:`(N,S)`, or
            :math:`(S)` when unbatched, where :math:`N` is the batch size and
            :math:`S` is the sequence length.
        :param self_attn_mask:
            The float mask that will be added to the attention weights before
            computing the self attention. *Shape:* :math:`(S,S)`, where
            :math:`S` is the sequence length.
        :param enc_out:
            The encoder output for the encoder-decoder attention. *Shape:*
            :math:`(N,S_{src},M_{enc})`, or :math:`(S_{src},M_{enc})` when
            unbatched, where :math:`N` is the batch size, :math:`S_{src}` is the
            source sequence length, and :math:`M_{enc}` is the encoder model
            size.
        :param enc_padding_mask:
            The boolean or float padding mask indicating which key positions to
            to ignore for the purpose of encoder-decoder attention. *Shape:*
            :math:`(N,S_{src})`, or :math:`(S_{src})` when unbatched, where
            :math:`N` is the batch size and :math:`S_{src}` is the source
            sequence length.
        :param state_bag:
            The state bag to use during an incremental evaluation.

        :returns:
            The decoded output. *Shape:* Same as ``x``.

        .. note::
            For a boolean padding mask, a ``True`` indicates that the
            corresponding key position is not allowed to attend. For a float
            padding mask, the mask values will be added to the attention
            weights.
        """

    def extra_repr(self) -> str:
        """:meta private:"""
        return f"model_dim={self.model_dim}"


@final
class StandardTransformerDecoderLayer(TransformerDecoderLayer):
    """Represents a Transformer decoder layer as described in
    :cite:t:`https://doi.org/10.48550/arxiv.1706.03762`."""

    self_attn: MultiheadAttention
    self_attn_norm: Optional[LayerNorm]
    self_attn_layer_norm: LayerNorm
    enc_dec_attn: Optional[MultiheadAttention]
    enc_dec_attn_layer_norm: Optional[LayerNorm]
    ffn: FeedForwardNetwork
    residual_scale: Optional[Parameter]
    ffn_layer_norm: LayerNorm
    dropout_p: float
    norm_order: TransformerNormOrder

    def __init__(
        self,
        self_attn: MultiheadAttention,
        enc_dec_attn: Optional[MultiheadAttention],
        ffn: FeedForwardNetwork,
        scale_residual: bool = False,
        dropout_p: float = 0.1,
        norm_order: TransformerNormOrder = TransformerNormOrder.POST,
        norm_eps: float = 1e-5,
        device: Optional[torch.device] = None,
        dtype: Optional[torch.dtype] = None,
    ) -> None:
        """
        :param self_attn:
            The self attention layer.
        :param enc_dec_attn:
            The encoder-decoder attention layer.
        :param ffn:
            The feed-forward network.
        :param scale_residual:
            If ``True``, scales residuals before adding them to the output of
            the feed-forward network. See
            :cite:t:`https://doi.org/10.48550/arxiv.2110.09456` for mor
            information.
        :param dropout_p:
            The dropout probability on outputs of the attention layers and the
            feed-forward network.
        :param norm_order:
            The Layer Normalization order to use.
        :param norm_eps:
            The epsilon value to add to the denominator of the
            :class:`~torch.nn.LayerNorm` modules for numerical stability.
        """
        model_dim = self_attn.model_dim

        super().__init__(model_dim)

        self_attn_layer_norm = LayerNorm(
            model_dim, norm_eps, device=device, dtype=dtype
        )

        if norm_order != TransformerNormOrder.POST:
            self.self_attn_layer_norm = self_attn_layer_norm

        self.self_attn = self_attn

        if norm_order == TransformerNormOrder.PRE_WITH_NORMFORMER:
            self.self_attn_norm = LayerNorm(
                model_dim, norm_eps, device=device, dtype=dtype
            )
        else:
            self.register_module("self_attn_norm", None)

        if norm_order == TransformerNormOrder.POST:
            self.self_attn_layer_norm = self_attn_layer_norm

        if enc_dec_attn is None:
            self.register_module("enc_dec_attn", None)
            self.register_module("enc_dec_attn_layer_norm", None)
        else:
            if enc_dec_attn.model_dim != model_dim:
                raise ValueError(
                    f"`model_dim` of `enc_dec_attn` ({enc_dec_attn.model_dim}) does not match `model_dim` of `self_attn` ({model_dim})."
                )

            enc_dec_attn_layer_norm = LayerNorm(
                model_dim, norm_eps, device=device, dtype=dtype
            )

            if norm_order != TransformerNormOrder.POST:
                self.enc_dec_attn_layer_norm = enc_dec_attn_layer_norm

            self.enc_dec_attn = enc_dec_attn

            if norm_order == TransformerNormOrder.POST:
                self.enc_dec_attn_layer_norm = enc_dec_attn_layer_norm

        if ffn.model_dim != model_dim:
            raise ValueError(
                f"`model_dim` of `ffn` ({ffn.model_dim}) does not match `model_dim` of `self_attn` ({model_dim})."
            )

        ffn_layer_norm = LayerNorm(model_dim, norm_eps, device=device, dtype=dtype)

        if norm_order != TransformerNormOrder.POST:
            self.ffn_layer_norm = ffn_layer_norm

        self.ffn = ffn

        if scale_residual:
            self.residual_scale = Parameter(
                torch.empty((model_dim,), device=device, dtype=dtype)
            )
        else:
            self.register_parameter("residual_scale", None)

        if norm_order == TransformerNormOrder.POST:
            self.ffn_layer_norm = ffn_layer_norm

        self.dropout_p = dropout_p

        self.norm_order = norm_order

        self.reset_parameters()

    def reset_parameters(self) -> None:
        """Reset the parameters of the module."""
        if self.residual_scale is not None:
            nn.init.ones_(self.residual_scale)

    @finaloverride
    def forward(
        self,
        x: Tensor,
        padding_mask: Optional[Tensor] = None,
        self_attn_mask: Optional[Tensor] = None,
        enc_out: Optional[Tensor] = None,
        enc_padding_mask: Optional[Tensor] = None,
        state_bag: Optional[IncrementalStateBag] = None,
    ) -> Tensor:
        x = self._forward_self_attn(x, padding_mask, self_attn_mask, state_bag)

        x = self._forward_enc_dec_attn(x, enc_out, enc_padding_mask, state_bag)

        x = self._forward_ffn(x)

        return x

    def _forward_self_attn(
        self,
        x: Tensor,
        padding_mask: Optional[Tensor],
        self_attn_mask: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag],
    ) -> Tensor:
        residual = x

        if self.norm_order != TransformerNormOrder.POST:
            x = self.self_attn_layer_norm(x)

        x = self.self_attn(
            x,
            keys=x,
            values=x,
            attn_mask=self_attn_mask,
            padding_mask=padding_mask,
            state_bag=state_bag,
        )

        if self.self_attn_norm is not None:
            x = self.self_attn_norm(x)

        if self.dropout_p > 0.0:
            x = F.dropout(x, self.dropout_p, self.training)

        x = x + residual

        if self.norm_order == TransformerNormOrder.POST:
            x = self.self_attn_layer_norm(x)

        return x

    def _forward_enc_dec_attn(
        self,
        x: Tensor,
        enc_out: Optional[Tensor],
        enc_padding_mask: Optional[Tensor],
        state_bag: Optional[IncrementalStateBag],
    ) -> Tensor:
        if self.enc_dec_attn is None:
            if enc_out is not None:
                raise ValueError("`enc_out` must be `None` for decoder-only attention.")

            return x

        if enc_out is None:
            raise ValueError(
                "`enc_out` must not be `None` for encoder-decoder attention."
            )

        residual = x

        if self.norm_order != TransformerNormOrder.POST:
            x = cast(LayerNorm, self.enc_dec_attn_layer_norm)(x)

        x = self.enc_dec_attn(
            x,
            keys=enc_out,
            values=enc_out,
            padding_mask=enc_padding_mask,
            state_bag=state_bag,
        )

        if self.dropout_p > 0.0:
            x = F.dropout(x, self.dropout_p, self.training)

        x = x + residual

        if self.norm_order == TransformerNormOrder.POST:
            x = cast(LayerNorm, self.enc_dec_attn_layer_norm)(x)

        return x

    def _forward_ffn(self, x: Tensor) -> Tensor:
        residual = x

        if self.norm_order != TransformerNormOrder.POST:
            x = self.ffn_layer_norm(x)

        x = self.ffn(x)

        if self.dropout_p > 0.0:
            x = F.dropout(x, self.dropout_p, self.training)

        if self.residual_scale is not None:
            residual = torch.mul(self.residual_scale, residual)

        x = x + residual

        if self.norm_order == TransformerNormOrder.POST:
            x = self.ffn_layer_norm(x)

        return x

    def extra_repr(self) -> str:
        """:meta private:"""
        s = super().extra_repr()

        return f"{s}, dropout_p={self.dropout_p}, norm_order={self.norm_order}"

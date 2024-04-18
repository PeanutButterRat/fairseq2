# Copyright (c) Meta Platforms, Inc. and affiliates.
# All rights reserved.
#
# This source code is licensed under the BSD-style license found in the
# LICENSE file in the root directory of this source tree.

from fairseq2.recipes.cli import Cli, RecipeCommandHandler
from fairseq2.recipes.lm.chatbot import ChatbotCommand
from fairseq2.recipes.lm.instruction_finetune import (
    InstructionFinetuneConfig as InstructionFinetuneConfig,
)
from fairseq2.recipes.lm.instruction_finetune import (
    instruction_finetune_presets as instruction_finetune_presets,
)
from fairseq2.recipes.lm.instruction_finetune import (
    load_instruction_finetuner as load_instruction_finetuner,
)
from fairseq2.recipes.lm.text_complete import load_text_completer as load_text_completer
from fairseq2.recipes.lm.text_complete import (
    text_complete_presets as text_complete_presets,
)


def _setup_lm_cli(cli: Cli) -> None:
    group = cli.add_group("lm", help="Language Model recipes")

    group.add_command(
        "chatbot",
        ChatbotCommand(),
        help="run a terminal-based chatbot demo",
    )

    instruction_finetune_handler = RecipeCommandHandler(
        loader=load_instruction_finetuner,
        preset_configs=instruction_finetune_presets,
        default_preset="llama3_8b_instruct",
    )

    group.add_command(
        "instruction_finetune",
        instruction_finetune_handler,
        help="instruction-finetune a Language Model",
    )

    text_complete_handler = RecipeCommandHandler(
        loader=load_text_completer,
        preset_configs=text_complete_presets,
        default_preset="llama3_8b_instruct",
    )

    group.add_command(
        "text_complete",
        text_complete_handler,
        help="complete text prompts",
    )

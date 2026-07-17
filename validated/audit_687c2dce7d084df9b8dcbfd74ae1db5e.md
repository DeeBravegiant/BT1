Let me trace the code path for this question.

I need to trace the full code path from peer receipt through `collect_block_approval` into `Doomslug::on_approval_message` and check every guard.
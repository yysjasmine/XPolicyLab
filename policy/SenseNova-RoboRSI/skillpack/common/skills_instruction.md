Read each skill's main SKILL.md. Run bundled offline helpers through the documented CLI or explicitly documented Python API; routine use does not require reading their implementation. For a usage question, consult SKILL.md, then `--help` (or subcommand `--help`) with a small output limit. Forward helper stdout, not the entire command metadata envelope. Inspect only the relevant source excerpt if an actual error remains unexplained. Do not preload scripts or calibration tables into context. After compaction, reuse the known invocation or consult SKILL.md/help again. Preserve short helper commands, input/output contracts and result-file paths in continuation notes, rather than source code. Use reference images as needed.

Available skills:
- robodojo-spatial-localize: Calibrated RGB localization and read/write task spatial memory at /workspace/space.json. (file: /workspace/.agents/skills/robodojo-spatial-localize/SKILL.md)
- robo-general: Robot interface and setting-specific execution. (file: /workspace/.agents/skills/robo-general/SKILL.md)
- arrange_largest_number: Case strategy and public demonstration evidence. (file: /workspace/.agents/skills/arrange_largest_number/SKILL.md)

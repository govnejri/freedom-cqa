"""Сравнение двух разметок чек-листа (эталон vs эталон или эталон vs модель).

Обёртка над cqa.quality.compare: критерии, веса и шкалы — из конфига чек-листа.
Формат CSV: id,[family,language,channel],c01..c08; ячейка — ответ или «основной|альтернатива».

    python eval/compare.py eval/gold_draft.csv eval/gold_opus.csv [--diff]
        [--checklist config/checklists/freedom-cqa.yaml] [--by language|channel|family]
        [--only eval/holdout.txt | --exclude eval/holdout.txt] [--traps] [--json OUT]
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # запуск без установки пакета

from cqa.quality.compare import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())

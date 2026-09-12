# Проверенный демонстрационный прогон

Дата пересборки: 2026-09-12. Runtime отчёта: `monotone-report-v3`.

Команды выполняют offline baseline с полным registry. `run` блокирует
выполнение до готовности результата; выходной каталог должен быть новым:

```console
uv sync --frozen --no-editable
MONOTONE_CALIBRATE_LLM_ENABLED=false uv run --frozen --no-sync monotone-calibrate run examples/demo.csv \
  --output outputs/demo-rerun --no-dotenv
uv run --frozen --no-sync monotone-calibrate verify outputs/demo-rerun
```

Сохранённый [отчёт](../examples/demo-output/report.html) использует профиль
`fast`, 10 повторов grouped validation и 200 bootstrap resamples:

- 32 входные строки, 30 использованы, 2 пропущены (`INVALID_ROWS_SKIPPED`);
- P1 refit `R² = 0.9973525132`, grouped OOF `R² = 0.9962296148`;
- P2 global refit `R² = 0.9999863916`, grouped OOF `R² = 0.9994967195`;
- граница P2: `x = 11.5`, доли сегментов `40% / 60%`;
- relative OOF MSE uplift P2: `0.8665174728` (около `86.65%`);
- paired bootstrap 90% interval uplift: `[0.7735673546, 0.9344765085]`;
- состояние решения: `UNSTABLE_SELECTION`, рекомендация: `P1`;
- LLM: `DISABLED`, scope `deterministic_full_registry`, portfolio `71`
  (`8` P1 и `63` P2), provenance `llm-formulas-v1`.

P2 имеет меньшую ошибку, но не проходит проверку устойчивости выбора:
наиболее частая пара семейств встречается только в 38% внешних fits.
Поэтому текущая политика сохраняет рекомендацию P1. Обе модели доступны
отдельно; рекомендация не скрывает метрики более точного варианта.

## Рекомендованная функция

```text
f(x)=1.612+26.434/(1+exp(-6.139*(((x-0.000)/29.000)-0.717)))
```

Коэффициенты округлены до тысячных. Канонический исполнимый артефакт —
[recommended-model.json](../examples/demo-output/recommended-model.json).
`verify` проверяет 11 content artifacts и semantic binding модели.
[predict.csv](../examples/predict.csv) и
[demo-predictions.csv](../examples/demo-predictions.csv) показывают применение:
три `OK`, одна строка `OUT_OF_DOMAIN` без экстраполяции и одна `INVALID_X`.

## Примеры с LLM

Готовые реальные OpenRouter-запуски нового поиска формул:

- [Плавная корневая зависимость](../examples/llm-sqrt-output/report.html):
  новая LLM-формула имеет наименьший holdout RMSE в этом примере.
- [Два режима](../examples/llm-two-regime-output/report.html):
  алгоритмическая P2 точнее новой LLM-формулы.

Каждый пакет содержит три кандидата и общий holdout. Данные, команды
OpenRouter/GigaChat и применение готовых формул описаны в
[examples/README.md](../examples/README.md).

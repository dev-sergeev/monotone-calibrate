# Проверенный демонстрационный прогон

Дата пересборки: 2026-07-22.

Команды выполняют offline baseline с полным registry. `run` является
блокирующим и печатает JSON только после завершения:

```console
uv sync --frozen
uv run --frozen --no-sync monotone-calibrate run examples/demo.csv \
  --output examples/demo-output --no-dotenv
uv run --frozen --no-sync monotone-calibrate verify examples/demo-output
uv run --frozen --no-sync monotone-calibrate predict \
  examples/demo-output/recommended-model.json examples/predict.csv \
  --output examples/demo-predictions.csv
```

## Результат

- принято 30 из 32 строк; две невалидные строки пропущены с
  `INVALID_ROWS_SKIPPED`;
- P1 refit `R² = 0.9973525132`, grouped OOF `R² = 0.9962296148`;
- P2 global refit `R² = 0.9999864821`, grouped OOF `R² = 0.9995557661`;
- граница P2: `x = 11.5`, доли сегментов `40% / 60%`;
- segment refit `R²`: left `0.9971446020`, right `0.9999803536`;
- relative OOF MSE uplift P2: `0.8821781069` (около `88.22%`);
- paired bootstrap 90% interval uplift:
  `[0.8029444045, 0.9413716749]`;
- состояние решения: `CLEAR_PRACTICAL_UPLIFT`, рекомендация: `P2`;
- LLM-SR: `DISABLED`, scope `deterministic_full_registry`, portfolio `71`
  typed hypotheses (`8` P1 и `63` P2);
- output schema в provenance: `llm-sr-hypotheses-v1`.

## Рекомендованная функция

Коэффициенты и breakpoint исполнимой модели округлены до тысячных:

```text
f(x)=2.009
     +1.675*((x-0.000)/11.500)
     +0.060*((x-0.000)/11.500)^2
     -0.017*((x-0.000)/11.500)^3, x <= 11.500

f(x)=3.727
     +20.969*((x-11.500)/17.500)
     +0.082*((x-11.500)/17.500)^2
     -0.060*((x-11.500)/17.500)^3, x > 11.500
```

Обе ветви имеют direction `increasing`, проходят certificate и соединяются в
одной direction-safe grid cell. Канонический исполнимый артефакт —
[`recommended-model.json`](../examples/demo-output/recommended-model.json),
визуальный отчёт — [`report.html`](../examples/demo-output/report.html).

`verify` подтвердил `8` content artifacts и semantic binding модели. `predict`
обработал пять строк: три `OK`, одну `OUT_OF_DOMAIN` без экстраполяции и одну
`INVALID_X`.

Этот demo не тестирует внешний provider. Для текущего LLM-SR нужен отдельный
output directory, настроенный `.env` и `--llm-symbolic-search`; исторический
[`llm-demo-output`](../examples/llm-demo-output) к этой schema не относится.

# Проверенный демонстрационный прогон

Команда:

```console
uv run monotone-calibrate run examples/demo.csv --output examples/demo-output --no-dotenv
uv run monotone-calibrate verify examples/demo-output
uv run monotone-calibrate predict examples/demo-output/recommended-model.json examples/predict.csv --output examples/demo-predictions.csv
```

Результат реального прогона:

- принято 30 из 32 строк; две невалидные строки пропущены с
  `INVALID_ROWS_SKIPPED`;
- P1 refit `R² = 0.997353`, grouped OOF `R² = 0.996226`;
- P2 global refit `R² = 0.999986`, grouped OOF `R² = 0.999628`;
- граница P2: `x = 11.5`, доли сегментов `40% / 60%`;
- segment refit `R²`: left `0.997140`, right `0.999980`;
- relative OOF MSE uplift P2: `0.901340` (около `90.13%`);
- paired bootstrap 90% interval uplift: `[0.841885, 0.949891]`;
- состояние решения: `CLEAR_PRACTICAL_UPLIFT`, рекомендация: `P2`;
- LLM advisor: `DISABLED`.

Точная рекомендованная формула:

```text
f(x)=2.0094308243135979
     +1.6753355030476578*((x-0)/11.5)
     +0.060244318192943513*((x-0)/11.5)^2
     -0.016958969031181326*((x-0)/11.5)^3, x <= 11.5

f(x)=3.7280516765230169
     +20.968640441001021*((x-11.5)/17.5)
     +0.081767287341953931*((x-11.5)/17.5)^2
     -0.059995399469407573*((x-11.5)/17.5)^3, x > 11.5
```

Обе ветви имеют общий fitted join в `x=11.5`, глобальная монотонность
сертифицирована. Канонический исполнимый вариант —
[`recommended-model.json`](../examples/demo-output/recommended-model.json), а
визуальное заключение — [`report.html`](../examples/demo-output/report.html).

`verify` подтвердил 8 артефактов и semantic binding модели. `predict` обработал
пять строк: три `OK`, одну `OUT_OF_DOMAIN` без экстраполяции и одну `INVALID_X`.

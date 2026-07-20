# PROTOTYPE — profiled fitting strategy v2.2

Этот throwaway-прототип отвечает на один вопрос: должен ли production seam перечислять допустимые tie-safe ячейки границы и при каждом фиксированном наборе `c/shape` профилировать неограниченный линейный блок, или достаточно совместного локального поиска всех параметров.

Сравниваются три стратегии:

- `profile_cells` — детерминированный кандидат на production seam;
- `joint_cells` — equal-start локальный baseline для нелинейных семейств; для polynomial hinge используется отдельно объявленная scalar-oracle специализация;
- `global_profiled_reference` — более дорогой stochastic best-known reference, но не доказательство глобального минимума.

Одна воспроизводимая команда запускает полный machine-gated batch:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
uv run --python 3.12 \
  --with 'numpy==2.4.2' --with 'scipy==1.18.0' \
  python prototypes/fitting_strategy/app.py --batch
```

Без `--batch` запускается TUI:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 \
uv run --python 3.12 \
  --with 'numpy==2.4.2' --with 'scipy==1.18.0' \
  python prototypes/fitting_strategy/app.py
```

Файлы:

- `profiled_logic.py` — чистая fitting/certificate-логика без I/O и wall-clock;
- `experiment.py` — сценарии и заранее заданные decision gates;
- `app.py` — TUI, batch JSON, diagnostic-only измерение времени и environment provenance.

Проверяются P1 и P2 в обоих направлениях, poly1, обе rate-ветви exponential и logistic, exact continuity, независимый analytic certificate с fault injection, `40–60%`, одинаковые `x`, exact `40/60`, раздельные no-split/insufficient states, canonical collapse, competing basins, saturation/bound hit, реальное исчерпание solver budget, перестановка строк, обратный порядок стартов, affine scaling и binary64 quantization stress. Tie-группы и boundary cells всегда строятся по исходным canonical binary64 `x`; если разные `x` схлопываются в одно нормализованное `t`, P1/P2 останавливаются до solver со статусом `NONINVERTIBLE_X_SCALE` и warning `LOW_X_RESOLUTION`, а не объявляют их совпадающими. Узкое нарушение производной кубика показывает, почему grid-check не является сертификатом.

Зафиксированный batch 2026-07-16 вернул `decision=PROFILE_CELLS`: все 11 обязательных gate прошли. Decision-bearing efficiency gate сравнивает детерминированные outer-evaluation counts и проверяет точный учёт inner LSQ; одиночный wall-clock публикуется только как диагностика и не может изменить решение. Подробный численный вердикт находится в [`docs/specification/05-fitting-strategy-verdict.md`](../../docs/specification/05-fitting-strategy-verdict.md).

Это не production registry, не nested-validation engine, не calibration практического uplift и не пользовательское приложение. Полный recovery/confusion всех семейств, ordered pairs, noise designs и policy thresholds остаётся обязательным acceptance gate будущего implementation-handoff.

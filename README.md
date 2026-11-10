# NyanwHair 1.0

Build expressive stylized hair one strand at a time. Shape the guides and NyanwHair stitches neighbouring segments into a continuous mesh. Grow branches, open sharp tips, and add or remove polygons along and across each strand as you work.

**Blender 5.1+ · English / Русский**

## What it does

- Live surfaces with automatic stitching and local topology transitions.
- Per-segment detail, width, convexity and cross-section distribution.
- Local density with a smooth falloff around selected points.
- Simple branching, E-Face and curve construction from a single point.
- Independent hair groups, linked Mirror and an optional reverse surface for volume.
- Mesh conversion with Smooth/Sharp and top-to-bottom UV islands; mirrored UV can overlap or stay separate.
- Six colour themes and viewport MatCap, Studio or Flat lighting.

## Controls

| Shortcut | Action |
| --- | --- |
| E | Extend a free end or create a branch |
| E from one point → wheel | Set intermediate points; move the mouse to bend the curve |
| Ctrl+R | Hair Loop: split a segment or add a guide support point |
| M | Merge points while preserving strand settings |
| Shift+wheel | Width across the segment |
| Ctrl+Shift+wheel | Detail along the segment |
| Ctrl+wheel | Convexity |
| Alt+wheel | Guide smoothing |
| LMB / Enter · Esc / RMB | Confirm · cancel |

## Strand settings

**Start / End** define the direction of a segment. Each boundary can be an edge or a single point. The × button detects that boundary again while keeping the other end.

**Detail** controls rows along the strand. **Width** controls columns across it. **Convexity** shapes the cross-section; **Distribution** shifts the columns across the width.

**Local density** adds geometry near selected points without changing the guide shape. **Radius** sets how far the extra density spreads along the guides, with a smooth fade to the regular grid.

**Volume** adds a reverse surface with its own width and convexity. **Mirror UV** lets reflected strands share the original texture space or use separate islands.

Choose **RU / EN** in **Additional**. The initial language follows Blender; other languages use English.

[Download](https://github.com/Nyanwake/NyanwHair/releases/tag/v1.0.0) · [GPL-3.0](LICENSE)

---

## Русский

Формируй стилизованные локоны без ручной перестройки сетки после каждой правки. Несколько направляющих задают силуэт, а NyanwHair автоматически сшивает соседние пряди в единый меш. Меняй плотность вдоль и поперёк локона, раскрывай кончики и добавляй ответвления по ходу работы.

**Возможности:** Live-предпросмотр, согласованные стыки, локальное уплотнение, E/E-Face, Hair Loop, Merge, группы волос, связанный Mirror, объёмная обратная сторона и Mesh с UV. Шесть цветовых тем, MatCap/Studio/Flat.

**Начало / Конец** задают направление сегмента. Торцом может быть ребро или одна точка. Крестик заново определяет выбранный торец, сохраняя противоположный.

**Детализация** — ряды вдоль пряди; **Ширина** — колонки поперёк. **Выпуклость** задаёт поперечный профиль, **Распределение** смещает колонки по ширине.

**Уплотнение** добавляет полигоны возле выбранных точек, сохраняя форму направляющих. **Радиус** задаёт область влияния вдоль кривых с плавным переходом к обычной сетке.

Горячие клавиши: **E** — вытягивание и ветвление; **Ctrl+R** — Loop; **M** — Merge. **Shift+колесо** — ширина, **Ctrl+Shift+колесо** — детализация, **Ctrl+колесо** — выпуклость, **Alt+колесо** — сглаживание.

Язык **RU / EN** выбирается в **«Дополнительно»**.

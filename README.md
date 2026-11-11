# NyanwHair 1.07

Build expressive stylized hair one strand at a time. Shape the guides and NyanwHair stitches neighbouring segments into a continuous mesh. Grow branches, open sharp tips, and add or remove polygons along and across each strand as you work.

**Blender 5.1+ · English / Русский**

## What it does

- Live surfaces with automatic stitching and local topology transitions.
- Per-segment detail, width, convexity and automatic cross-section rounding.
- Local density with a smooth falloff around selected points.
- Simple branching, closed strand extrusion and curve construction from a single point.
- Hair cap preset, independent groups, editable reverse sides and Mirror with Apply.
- Mesh conversion with Smooth/Sharp and top-to-bottom UV islands; mirrored UV can overlap or stay separate.
- Six colour themes and viewport MatCap, Studio or Flat lighting.

## Controls

| Shortcut | Action |
| --- | --- |
| E | Extend a free end or edge chain; give whole selected strands a closed reverse |
| E from one point → wheel | Set intermediate points; move the mouse to bend the curve |
| E on a free wire chain | Create one editable segment from the selected points or edges |
| Ctrl+R | Hair Loop: split a segment or add a guide support point |
| M | Merge points while preserving strand settings |
| V | Rip a guide point; supports sparse boundary corners |
| Shift+wheel | Width across the segment |
| Ctrl+Shift+wheel | Detail along the segment |
| Ctrl+wheel | Convexity |
| Alt+wheel | Guide smoothing |
| LMB / Enter · Esc / RMB | Confirm · cancel |

## Strand settings

**Start / End** define the direction of a segment. Each boundary can be an edge or a single point. The × button detects that boundary again while keeping the other end.

E continues a selected end, extrudes edge chains including internal edges, or gives whole strands a closed volume. Two opposite guide edges create a branch from the middle.

Extruded side walls run along the strand as whole segments. Connections across the thickness start with smoothing 0.

New strands and the **hair cap preset** start at the **3D cursor**. The cap uses Mirror X.

Click the **3 · Segment settings / 2 · Segment boundaries / 1 · Points** headers to switch selection modes. Selecting every point of a segment also selects that segment in modes 1 and 2.

Header icons follow the colour theme. Back faces are slightly darker. **Flip normals**, below Local density, reverses selected segments while preserving their shape; convexity changes sign to match the new front side.

**Detail** controls rows along the strand. **Width** controls columns across it. **Convexity** shapes the cross-section; **Distribution** shifts the columns across the width.

Side neighbours add a small automatic base to **Convexity**. The usual cross-section stays in place; the parameter adjusts it in both directions. Straight thickness connections stay flat.

**Local density** adds geometry near selected points without changing the guide shape. **Radius** sets how far the extra density spreads along the guides, with a smooth fade to the regular grid.

**Create reverse side** keeps the front segment and adds two editable rear segments with their own centre guide. Offset and convexity have a live preview. **Connect edges** joins the sides, without walls at zero offset; the end caps stay open. **Mirror UV** lets reflected strands share the original texture space or use separate islands.

**Mirror · Merge** joins reflected halves within the chosen distance. **Apply** turns the active group’s reflections into editable guides in the same object and disables its Mirror axes. **M → Mirror X/Y/Z** places selected points on a mirror plane.

Choose **RU / EN** in **Additional**. The initial language follows Blender; other languages use English.

[Download](https://github.com/Nyanwake/NyanwHair/releases/tag/v1.0.7) · [GPL-3.0](LICENSE)

---

## Русский

Формируй стилизованные локоны без ручной перестройки сетки после каждой правки. Несколько направляющих задают силуэт, а NyanwHair автоматически сшивает соседние пряди в единый меш. Меняй плотность вдоль и поперёк локона, раскрывай кончики и добавляй ответвления по ходу работы.

**Возможности:** Live-предпросмотр, согласованные стыки, локальное уплотнение, E/E-Face, Hair Loop, Merge, группы волос, связанный Mirror, редактируемая обратная сторона и Mesh с UV. Шесть цветовых тем, MatCap/Studio/Flat.

**Начало / Конец** задают направление сегмента. Торцом может быть ребро или одна точка. Крестик заново определяет выбранный торец, сохраняя противоположный.

E продолжает выбранный торец, вытягивает цепочки, включая внутренние рёбра, а для целых локонов создаёт замкнутый объём. Два противоположных ребра создают ответвление из середины.

E на свободной цепочке точек или рёбер создаёт один сегмент. Боковины объёма идут цельными сегментами вдоль локона; соединения по толщине получают сглаживание 0.

Новый локон и пресет **«Создать шапку»** появляются у **3D-курсора**. У шапки включён Mirror X.

Заголовки **3 · Настройки сегмента / 2 · Края сегмента / 1 · Точки** переключают режим выделения. В режимах 1 и 2 выбор всех точек сегмента выделяет и сам сегмент.

Значки заголовков следуют цветовой теме. Обратная сторона слегка затемнена. **Перевернуть нормали**, под локальной плотностью, разворачивает выбранные сегменты с сохранением формы; знак выпуклости меняется под новую лицевую сторону.

**Детализация** — ряды вдоль пряди; **Ширина** — колонки поперёк. **Выпуклость** задаёт поперечный профиль, **Распределение** смещает колонки по ширине.

К **выпуклости** автоматически прибавляется небольшая поправка по углу боковых соседей. Обычный поперечный профиль сохраняется, параметр регулирует его в обе стороны. Прямые соединения по толщине остаются плоскими.

**Уплотнение** добавляет полигоны возле выбранных точек, сохраняя форму направляющих. **Радиус** задаёт область влияния вдоль кривых с плавным переходом к обычной сетке.

**Создать обратную сторону** сохраняет лицевой сегмент и добавляет два задних со своей центральной направляющей. Смещение и выпуклость видны в предпросмотре. **Соединить края** сшивает бока, при нулевом смещении без стенок; торцы остаются открытыми.

**Mirror · Merge** соединяет половины в пределах допуска. **Применить** превращает отражения активной группы в редактируемые направляющие того же объекта и выключает её оси Mirror. **M → На плоскость Mirror X/Y/Z** ставит выбранные точки на плоскость зеркала.

Горячие клавиши: **E** — продолжение торца, непрерывной цепочки рёбер или ветвление; **Ctrl+R** — Loop; **M** — Merge; **V** — разделение точки, включая краевой угол сегмента. **Shift+колесо** — ширина, **Ctrl+Shift+колесо** — детализация, **Ctrl+колесо** — выпуклость, **Alt+колесо** — сглаживание.

Язык **RU / EN** выбирается в **«Дополнительно»**.

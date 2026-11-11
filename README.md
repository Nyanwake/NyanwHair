# NyanwHair 1.07

Shape expressive stylized hair with a few guides. NyanwHair stitches neighbouring segments into a continuous mesh as you work. Grow branches easily and adjust polygon density both along and across a strand.

**Blender 5.1+ · English / Русский**

[Download](https://github.com/Nyanwake/NyanwHair/releases/latest) · [Русский](#русский) · [GPL-3.0](LICENSE)

## What it does

- Live hair surfaces with automatic stitching and local topology transitions.
- Adjustable detail, width and convexity, plus local density around selected points.
- Strand extension, branching, edge extrusion and editable closed volumes.
- A hair cap preset, independent hair groups, Mirror and editable reverse sides.
- Conversion to an independent Mesh with guide seams, Smooth/Sharp shading and optional UVs.

**Create new strand** and **Create hair cap** place a new object at the **3D cursor**. The cap starts with Mirror X. Shape the guides in **Edit Mode**; **Convert to Mesh** creates a separate result and keeps the guides editable.

Click the **1 · Points / 2 · Segment boundaries / 3 · Segment settings** headers to switch selection modes. In modes 1 and 2, selecting every point of a segment also selects that segment for its settings.

## E — selection decides the action

| Select | Result |
| --- | --- |
| Only a free Start/End boundary: a point or an edge | Continues the existing strand. A pointed end opens as you pull it. Several free ends can be extended together. |
| One point that is not a free marked end | Extends a guide wire. The wheel adds intermediate points and enters curve bending. |
| An open wire chain without faces, selected as points or edges | Creates one editable surface segment from the chain. |
| A boundary or internal edge, or a continuous edge chain | Extrudes an editable strip from the shared base. Internal edges work on closed strands too. |
| Several separate edge chains | Extrudes all selected chains together as separate strips. |
| A closed edge contour, without selecting whole segments | Extrudes walls around the contour. |
| Exactly two separate, opposite side edges of one segment | Creates a branch from the middle, keeping the portions outside the selected band. |
| One or more whole strands | Keeps the front and adds an editable reverse and side walls to make a closed volume. |
| A face or face region attached to an existing surface | Moves the selected surface out and adds side walls, keeping its outer cap. |

Whole-segment and opposite-side-edge selections take priority over generic chains. End extension requires only the marked end points; adding the rest of an edge chain uses chain extrusion.

During E: move the mouse, use **X / Y / Z** to constrain an axis, enter a distance, or hold **Shift** for precision. **LMB / Enter** confirms; **RMB / Esc** cancels.

For a **curve from one point**, first place the new end, then scroll the wheel. Move the mouse to bend the curve; scroll again to change the number of intermediate points.

Volume side walls run as whole segments along the strand. Connections across the thickness start with smoothing **0**.

## Shape and density

| Parameter | Meaning |
| --- | --- |
| **Start / End** | The two boundaries that define segment flow. Each can be an edge or a point. The **×** button detects that end again while keeping the other. |
| **Detail** | Polygon density along the strand. |
| **Width** | Number of columns across the segment. |
| **Convexity** | Cross-section bulge. Side neighbours provide a small automatic base; positive and negative values adjust it in either direction. |
| **Distribution** | Column placement across the width: **0** is uniform, **+** concentrates columns toward the centre, **−** toward the edges. |
| **Smoothing** | Guide tangent smoothing: **1** is smooth; **0** points toward the next control point. |

**Local density** adds polygons around selected points without changing the guides. **Density 1** is the regular grid; larger values refine it. **Radius** controls the influence along the guides, with a smooth falloff. **Reset** clears the selected points; **Reset all** clears the active group's local density.

**Flip normals**, below Local density, reverses selected segments while preserving their shape. Back faces are slightly darker, and positive convexity follows the chosen front side.

## Reverse sides, groups and Mirror

**Create reverse side** keeps the front and adds two editable rear segments with their own centre guide. **Offset** and **Convexity** update the preview immediately. **Connect edges** joins the sides; at zero offset it joins them directly without intermediate walls. The end caps stay open.

**Hair groups** keep parts of a hairstyle independently editable within one object. Add or remove groups, move selected segments between them, and assign group colours. Other groups display their surfaces while the active group is edited. Empty groups offer **Create strand in group**.

**Mirror X / Y / Z** reflects the active group. **Merge** joins halves on the mirror planes within the chosen distance, **0.001** by default. **Apply** makes the reflections editable in the same object and switches that group's axes off. **M → Mirror X/Y/Z** places selected points exactly on a mirror plane.

## Mesh and UV

**Convert to Mesh** creates an independent Mesh with guide seams and Smooth/Sharp shading. The original guides are retained, with their preview turned off. Enable **UV** for islands oriented from top to bottom along strand flow. Compatible segments join into larger islands; sharp branches and incompatible joins stay separate.

**Stitch angle**, **60°** by default, limits which surfaces and flow directions can join. **Mirror UV** is on by default: reflections share the original UV space. Turn it off for separate mirrored islands.

## Viewport and interface

Six colour themes, group colours, brightness, segment highlighting and mesh-wire opacity keep the guides readable. **Transparency** fades the whole preview for working over a reference. Lighting follows the viewport's **MatCap / Studio / Flat** mode; **Smooth + Sharp** controls surface shading. The theme library is [themes.json](themes.json).

**Live** controls surface updates; the eye button controls preview visibility. The collapsible **Hotkeys** panel provides large buttons for mouse or tablet use.

In **Additional**: choose **EN / RU**, refresh the row layout, or disable NyanwHair on the object. The initial language follows Blender: Russian for a Russian interface, English otherwise.

## Shortcuts

For the active NyanwHair guide in Edit Mode.

| Shortcut | Action |
| --- | --- |
| **1 / 2 / 3** | Points / edges / segments |
| **E** | Contextual extrusion; see the selection table above |
| **Ctrl+R** | Hair Loop: cut along a longitudinal mesh line, or add a support point by hovering a guide |
| **M** | Merge: Center, Active, First, Last, Cursor or By Distance; mirror planes when enabled |
| **V** | Rip a guide point, including a sparse boundary corner |
| **F / X** | Blender Fill / Delete; also available in Hotkeys |
| **Shift+wheel** | Width |
| **Ctrl+Shift+wheel** | Detail |
| **Ctrl+wheel** | Convexity |
| **Alt+wheel** | Smoothing, in steps of **0.1** |

---

## Русский

<details>
<summary>Возможности, параметры и все сценарии E</summary>

Формируй выразительные стилизованные локоны несколькими направляющими. NyanwHair автоматически сшивает соседние сегменты в единый меш. Добавляй ответвления и меняй плотность вдоль и поперёк пряди по ходу работы.

### Возможности

- Live-поверхность с автоматическим сшиванием и локальными переходами топологии.
- Детализация, ширина, выпуклость и локальное уплотнение возле выбранных точек.
- Продолжение локонов, ответвления, вытягивание рёбер и замкнутый редактируемый объём.
- Пресет шапки, независимые группы волос, Mirror и редактируемая обратная сторона.
- Преобразование в отдельный Mesh со швами по направляющим, Smooth/Sharp и UV по желанию.

**Создать новый локон** и **Создать шапку** добавляют объект у **3D-курсора**. У шапки включён Mirror X. Форма редактируется через направляющие в **Edit Mode**; **Преобразовать в Mesh** создаёт отдельный результат, сохраняя редактируемый каркас.

Заголовки **1 · Точки / 2 · Края сегмента / 3 · Настройки сегмента** переключают режим выделения. В режимах 1 и 2 выбор всех точек сегмента выделяет его и для изменения параметров.

### E — действие зависит от выделения

| Что выделить | Что получится |
| --- | --- |
| Только свободный торец «Начало» или «Конец»: точку либо ребро | Продолжение существующего локона. Острый кончик раскрывается при вытягивании. Можно продолжать несколько свободных торцов одновременно. |
| Одну точку, которая не является свободным отмеченным торцом | Новое ребро направляющей. Колесо добавляет промежуточные точки и включает изгиб кривой. |
| Незамкнутую цепочку без граней — точками или рёбрами | Один редактируемый сегмент поверхности из этой цепочки. |
| Граничное или внутреннее ребро, либо непрерывную цепочку рёбер | Вытягивание полосы с общей исходной базой. Внутренние рёбра работают и у замкнутых локонов. |
| Несколько раздельных цепочек рёбер | Все цепочки вытягиваются одновременно отдельными полосами. |
| Замкнутый контур рёбер без выделения сегментов целиком | Вытягивание стенок по контуру. |
| Ровно два отдельных противоположных боковых ребра одного сегмента | Ответвление из середины с сохранением частей вне выбранной полосы. |
| Один или несколько целых локонов | Лицевая сторона сохраняется, добавляются редактируемая обратная сторона и боковины. Получается замкнутый объём. |
| Грань или область граней, примыкающую к существующей поверхности | Выбранная поверхность вытягивается с боковыми стенками и сохранением внешней грани. |

Выбор сегмента целиком и пары противоположных боковых рёбер имеет приоритет перед обычными цепочками. Для продолжения торца нужны только его отмеченные точки; если добавить остальные рёбра цепочки, вытянется вся цепочка.

Во время E: мышь задаёт смещение, **X / Y / Z** ограничивают ось, число задаёт расстояние, **Shift** включает точное перемещение. **ЛКМ / Enter** — подтвердить, **ПКМ / Esc** — отменить.

Для **кривой из одной точки** сначала расположи новый конец, затем прокрути колесо. Мышь задаёт изгиб, дальнейшее вращение колеса меняет число промежуточных точек.

Боковины объёма идут цельными сегментами вдоль локона. Соединения по толщине получают сглаживание **0**.

### Форма и плотность

| Параметр | За что отвечает |
| --- | --- |
| **Начало / Конец** | Два торца, задающих направление сегмента. Каждый может быть ребром или точкой. Кнопка **×** заново определяет выбранный торец, сохраняя противоположный. |
| **Детализация** | Плотность полигонов вдоль локона. |
| **Ширина** | Количество колонок поперёк сегмента. |
| **Выпуклость** | Поперечный выгиб. Боковые соседи дают небольшую автоматическую основу, а значения в плюс и минус корректируют её в обе стороны. |
| **Распределение** | Расположение колонок по ширине: **0** — равномерно, **+** — ближе к центру, **−** — к краям. |
| **Сглаживание** | Касательные направляющей: **1** — плавные, **0** — направлены к следующей контрольной точке. |

**Локальная плотность** добавляет полигоны возле выбранных точек, сохраняя форму направляющих. **Плотность 1** — обычная сетка, большие значения уплотняют её. **Радиус** задаёт область влияния вдоль кривых с мягким спадом. **Сброс** очищает выбранные точки, **Сброс все** — локальное уплотнение активной группы.

**Перевернуть нормали**, под локальной плотностью, разворачивает выбранные сегменты с сохранением формы. Обратная сторона слегка затемнена; положительная выпуклость следует выбранной лицевой стороне.

### Обратная сторона, группы и Mirror

**Создать обратную сторону** сохраняет лицевой сегмент и добавляет два задних со своей центральной направляющей. **Смещение** и **выпуклость** сразу видны в предпросмотре. **Соединить края** сшивает бока; при нулевом смещении они соединяются напрямую, без промежуточных стенок. Торцы остаются открытыми.

**Группы волос** позволяют независимо редактировать части причёски внутри одного объекта. Группы можно добавлять и удалять, переносить между ними выбранные сегменты и назначать цвета. Пока редактируется активная группа, остальные показывают готовую поверхность. В пустой группе доступна кнопка **Создать локон в группе**.

**Mirror X / Y / Z** отражает активную группу. **Слияние** соединяет половины на плоскостях зеркала в пределах допуска, по умолчанию **0.001**. **Применить** делает отражения редактируемыми в том же объекте и выключает оси этой группы. **M → На плоскость Mirror X/Y/Z** точно ставит выбранные точки на плоскость зеркала.

### Mesh и UV

**Преобразовать в Mesh** создаёт независимый меш со швами по направляющим и Smooth/Sharp. Исходный каркас сохраняется, его предпросмотр выключается. Галочка **UV** добавляет развёртку с ориентацией вдоль локонов сверху вниз. Совместимые сегменты объединяются в крупные острова; резкие ответвления и неподходящие стыки разделяются.

**Угол сшивания**, по умолчанию **60°**, ограничивает объединение по углу поверхности и направлению локона. **Mirror UV** включена по умолчанию: отражения используют UV оригинала. Если выключить её, зеркальные части получат отдельные острова.

### Внешний вид и интерфейс

Шесть цветовых тем, цвета групп, яркость, подсветка сегментов и прозрачность сетки помогают читать форму. **Прозрачность** ослабляет весь предпросмотр для работы по референсу. Освещение следует режиму вьюпорта **MatCap / Studio / Flat**; **Smooth + Sharp** управляет сглаживанием поверхности. Библиотека цветовых схем — [themes.json](themes.json).

**Live** управляет обновлением поверхности, кнопка с глазом — её видимостью. Сворачиваемый раздел **Хоткеи** содержит крупные кнопки для мыши или планшета.

В **Дополнительно** можно выбрать **EN / RU**, обновить раскладку рядов или отключить NyanwHair на объекте. При первом запуске русский интерфейс Blender выбирает RU, остальные языки — EN.

### Горячие клавиши

Для активного каркаса NyanwHair в Edit Mode.

| Клавиши | Действие |
| --- | --- |
| **1 / 2 / 3** | Точки / рёбра / сегменты |
| **E** | Вытягивание по выделению — все сценарии в таблице выше |
| **Ctrl+R** | Hair Loop: разрез по продольной линии сетки или опорная точка при наведении на направляющую |
| **M** | Слияние: в центре, в активной, первой или последней точке, у курсора, по расстоянию; при Mirror — на его плоскость |
| **V** | Разделить точку направляющей, включая краевой угол редкого каркаса |
| **F / X** | Стандартные Fill / Delete Blender; также доступны в разделе «Хоткеи» |
| **Shift+колесо** | Ширина |
| **Ctrl+Shift+колесо** | Детализация |
| **Ctrl+колесо** | Выпуклость |
| **Alt+колесо** | Сглаживание с шагом **0.1** |

</details>

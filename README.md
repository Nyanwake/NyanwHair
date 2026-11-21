# NyanwHair 1.19 Preview

Shape expressive stylized hair with a few guides. NyanwHair stitches neighbouring segments into a continuous mesh as you work. Grow branches easily and adjust polygon density both along and across a strand.

**Blender 5.1+ · English / Русский · Native Windows / Linux x64**

[Download](https://github.com/Nyanwake/NyanwHair/releases/tag/v1.0.19-preview.1) · [Русский](#русский) · [GPL-3.0](LICENSE)

Preview with group and modelling-tool improvements.

## What it does

Accelerated geometry kernels reduce Live update work on Windows and Linux x64. Other systems use the Python fallback. Both paths keep the same current surface calculation. The active engine is shown in **Additional**.

- Live hair surfaces with automatic stitching, aligned rows around curls and local topology transitions.
- Adjustable detail, width and convexity, plus local density around selected points.
- Strand extension, branching, edge extrusion and editable closed volumes.
- A hair cap preset, independent hair groups, Mirror and editable reverse sides.
- Conversion to an independent Mesh with guide seams, Smooth/Sharp shading and optional UVs.

**Create new strand** and **Create hair cap** place a new object at the **3D cursor**. The cap starts with Mirror X. Shape the guides in **Edit Mode**; **Convert to Mesh** creates a separate result and keeps the guides editable.

Click the **1 · Points / 2 · Segment boundaries / 3 · Segment settings** headers to switch selection modes. In modes 1 and 2, selecting every point of a segment also selects that segment for its settings.

In **Object Mode**, click the visible Live surface to select its object, including Mirror copies. **Shift+click** adds or removes objects from the selection. Selected surfaces receive a subtle highlight.

## E — selection decides the action

| Select | Result |
| --- | --- |
| Only a free Start/End boundary: a point or an edge | Continues the existing strand. A pointed end stays a point; the wheel adds a curved extension. Several free ends can be extended together. |
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

For a **curve from a point or pointed strand end**, first place the new end, then scroll the wheel. Move across the strand to bend it and along it to shift the bend toward either end. Scroll again to change the number of intermediate points.

Volume side walls run as whole segments along the strand. Thickness connections start with smoothing **0** at the source and **1** at the new ends.

**Join segments** in **Hotkeys** removes the shared seam of two neighbouring segments and averages their detail, width, convexity and distribution. Select the shared edge or both segments. Outer guide points and their local settings are kept.

## Shape and density

| Parameter | Meaning |
| --- | --- |
| **Start / End** | The two boundaries that define segment flow. Each can be an edge or a point. Green marks **Start**, purple marks **End**, matching the button icons. The **×** button detects that end again while keeping the other. |
| **Detail** | Polygon density along the strand. |
| **Width** | Number of columns across the segment. |
| **Convexity** | Manual cross-section bulge. Zero adds no bulge; positive and negative values bend the profile in either direction. The cap preset starts with a gentle editable bulge. |
| **Distribution** | Column placement across the width: **0** is uniform, **+** concentrates columns toward the centre, **−** toward the edges. |
| **Smoothing** | Guide tangent smoothing: **1** is smooth; **0** separates the spline calculation at that guide end, so curves beyond the boundary do not bend the following section. |

**Local density** adds polygons around selected points without changing the guides. **Density 1** is the regular grid; larger values refine it. **Radius** controls the influence along the guides, with a smooth falloff. **Reset** clears the selected points; **Reset all** clears the active group's local density.

**Flip normals**, below Local density, reverses selected segments while preserving their shape. Back faces are slightly darker, and positive convexity follows the chosen front side.

Independent strands have separate caches: moving one does not rebuild the others. Groups share the object’s base density.

## Reverse sides, groups and Mirror

**Create reverse side** starts with Offset **0** and remembers the last confirmed offset for this session. It keeps the front and adds two editable rear segments with their own centre guide. **Offset** and **Convexity** update the preview immediately. **Connect edges** joins the sides; at zero offset it joins them directly without intermediate walls. The end caps stay open.

**Hair groups** keep parts of a hairstyle independently editable within one object. Add or remove groups, move selected segments between them, and assign group colours. Other groups display their surfaces while the active group is edited. Empty groups offer **Create strand in group**. Click another group’s surface to activate it. Adding a group moves fully selected segments in modes **1 / 2 / 3**, duplicating shared control points so neighbouring segments stay intact. Partial selections create an empty group. The eye in each group row hides it in the viewport.

**Mirror X / Y / Z** reflects the active group. **Merge** joins halves on the mirror planes within the chosen distance, **0.001** by default. **Apply** makes the reflections editable in the same object and switches that group's axes off. **M → Mirror X/Y/Z** places selected points exactly on a mirror plane.

**Ctrl+J** or **Join hair objects** in the Object context menu joins selected NyanwHair objects. All groups remain separate; duplicate names receive numeric suffixes such as .1 and .2. Mirror settings are retained when possible; mirrors in different local planes become editable to preserve their shape.

## Mesh and UV

**Convert to Mesh** creates an independent Mesh with guide seams and Smooth/Sharp shading. The original guides are retained, with their preview turned off. Enable **UV** for islands oriented from top to bottom along strand flow. Compatible segments join into larger islands; sharp branches and incompatible joins stay separate.

**Island spacing** sets UV padding and can be reduced to zero. **Stitch angle**, **60°** by default, limits which surfaces and flow directions can join. **Mirror UV** is on by default: reflections share the original UV space. Turn it off for separate mirrored islands.

## Viewport and interface

Six colour themes, group colours, brightness, segment highlighting and mesh-wire opacity keep the guides readable. **Transparency** fades the whole preview for working over a reference. Lighting follows the viewport's **MatCap / Studio / Flat** mode; **Smooth + Sharp** controls surface shading. In Wireframe, the surface automatically becomes translucent while guides stay readable. Native opaque control faces are suppressed while the preview is visible. The theme library is [themes.json](themes.json).

If Blender's **Retopology** overlay is enabled, **Hide Retopology fill** appears in **Additional** in Edit Mode. It switches off the native cage fill in this viewport while keeping other overlays enabled. This overlay is saved with the .blend file independently of RetopoFlow.

**Live** controls surface updates; the eye button controls preview visibility. The viewport and Mesh conversion use the same current curve lengths and row alignment, including while points move. The collapsible **Hotkeys** panel provides large buttons for mouse or tablet use.

**Point depth tolerance** in Appearance keeps slightly covered guide points visible; points deeper behind hair or other meshes stay hidden.

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
| **Alt+V** | Add one segment from an open edge chain, or a branch from a point |
| **K** | Blender Knife on the curved hair silhouette; new cut points follow the Live surface |
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

Предварительная сборка с улучшениями групп и инструментов моделирования.

### Возможности

На Windows и Linux x64 массовые расчёты Live выполняет ускоренное ядро. На других системах работает Python. Оба варианта используют текущую форму направляющих; активное ядро видно в **Дополнительно**.

- Live-поверхность со сшиванием, выравниванием рядов на завитках и локальными переходами топологии.
- Детализация, ширина, выпуклость и локальное уплотнение возле выбранных точек.
- Продолжение локонов, ответвления, вытягивание рёбер и замкнутый редактируемый объём.
- Пресет шапки, независимые группы волос, Mirror и редактируемая обратная сторона.
- Преобразование в отдельный Mesh со швами по направляющим, Smooth/Sharp и UV по желанию.

**Создать новый локон** и **Создать шапку** добавляют объект у **3D-курсора**. У шапки включён Mirror X. Форма редактируется через направляющие в **Edit Mode**; **Преобразовать в Mesh** создаёт отдельный результат, сохраняя редактируемый каркас.

Заголовки **1 · Точки / 2 · Края сегмента / 3 · Настройки сегмента** переключают режим выделения. В режимах 1 и 2 выбор всех точек сегмента выделяет его и для изменения параметров.

В **Object Mode** объект выбирается кликом по видимой Live-поверхности, включая Mirror. **Shift+клик** добавляет или снимает выделение. Выбранная поверхность слегка подсвечивается.

### E — действие зависит от выделения

| Что выделить | Что получится |
| --- | --- |
| Только свободный торец «Начало» или «Конец»: точку либо ребро | Продолжение существующего локона. Острый кончик остаётся точкой; колесо добавляет кривое продолжение. Можно продолжать несколько свободных торцов одновременно. |
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

Для **кривой из точки или кончика локона** сначала расположи новый конец, затем прокрути колесо. Движение мыши поперёк локона задаёт изгиб, вдоль — положение изгиба ближе к началу или концу. Колесо меняет число промежуточных точек. Кончик остаётся одной точкой.

Боковины объёма идут цельными сегментами вдоль локона. Соединения по толщине получают сглаживание **0** у исходных точек и **1** у новых.

**Объединить сегменты** в **Хоткеях** убирает общий стык двух соседних сегментов и усредняет детализацию, ширину, выпуклость и распределение. Выдели общее ребро или оба сегмента. Внешние точки направляющих и их локальные настройки сохраняются.

### Форма и плотность

| Параметр | За что отвечает |
| --- | --- |
| **Начало / Конец** | Два торца, задающих направление сегмента. Каждый может быть ребром или точкой. **Начало** отмечено зелёным, **Конец** — фиолетовым; значки кнопок повторяют эти цвета. Кнопка **×** заново определяет выбранный торец, сохраняя противоположный. |
| **Детализация** | Плотность полигонов вдоль локона. |
| **Ширина** | Количество колонок поперёк сегмента. |
| **Выпуклость** | Ручной поперечный выгиб. При нуле дополнительного выгиба нет; значения в плюс и минус меняют его в обе стороны. В пресете шапки уже задан лёгкий редактируемый выгиб. |
| **Распределение** | Расположение колонок по ширине: **0** — равномерно, **+** — ближе к центру, **−** — к краям. |
| **Сглаживание** | Касательные направляющей: **1** — плавные, **0** — независимый расчёт от этого конца направляющей; изгибы за границей не влияют на следующий участок. |

**Локальная плотность** добавляет полигоны возле выбранных точек, сохраняя форму направляющих. **Плотность 1** — обычная сетка, большие значения уплотняют её. **Радиус** задаёт область влияния вдоль кривых с мягким спадом. **Сброс** очищает выбранные точки, **Сброс все** — локальное уплотнение активной группы.

**Перевернуть нормали**, под локальной плотностью, разворачивает выбранные сегменты с сохранением формы. Обратная сторона слегка затемнена; положительная выпуклость следует выбранной лицевой стороне.

Независимые локоны используют отдельные кэши. Движение одного не перестраивает остальные; группы используют общую базовую плотность объекта.

### Обратная сторона, группы и Mirror

**Создать обратную сторону** начинает со смещения **0** и запоминает последнее подтверждённое смещение на время сеанса. Команда сохраняет лицевой сегмент и добавляет два задних со своей центральной направляющей. **Смещение** и **выпуклость** сразу видны в предпросмотре. **Соединить края** сшивает бока; при нулевом смещении они соединяются напрямую, без промежуточных стенок. Торцы остаются открытыми.

**Группы волос** позволяют независимо редактировать части причёски внутри одного объекта. Клик по поверхности другой группы делает её активной. При добавлении группы полностью выбранные сегменты в режимах **1 / 2 / 3** переносятся в неё; общие с оставшимися сегментами точки дублируются. При частичном выделении создаётся пустая группа. Глазик в строке группы скрывает её во вьюпорте. Группы можно добавлять и удалять, переносить между ними выбранные сегменты и назначать цвета. Пока редактируется активная группа, остальные показывают готовую поверхность. В пустой группе доступна кнопка **Создать локон в группе**.

**Mirror X / Y / Z** отражает активную группу. **Слияние** соединяет половины на плоскостях зеркала в пределах допуска, по умолчанию **0.001**. **Применить** делает отражения редактируемыми в том же объекте и выключает оси этой группы. **M → На плоскость Mirror X/Y/Z** точно ставит выбранные точки на плоскость зеркала.

**Ctrl+J** или **Объединить объекты волос** в контекстном меню объединяет выбранные объекты NyanwHair. Все группы остаются отдельными; повторяющиеся имена получают суффиксы .1, .2 и далее. Настройки Mirror сохраняются, а отражения в другой локальной плоскости становятся редактируемыми для сохранения формы.

### Mesh и UV

**Преобразовать в Mesh** создаёт независимый меш со швами по направляющим и Smooth/Sharp. Исходный каркас сохраняется, его предпросмотр выключается. Галочка **UV** добавляет развёртку с ориентацией вдоль локонов сверху вниз. Совместимые сегменты объединяются в крупные острова; резкие ответвления и неподходящие стыки разделяются.

**Отступ между островами** задаёт расстояние на UV и может быть уменьшен до нуля. **Угол сшивания**, по умолчанию **60°**, ограничивает объединение по углу поверхности и направлению локона. **Mirror UV** включена по умолчанию: отражения используют UV оригинала. Если выключить её, зеркальные части получат отдельные острова.

### Внешний вид и интерфейс

Шесть цветовых тем, цвета групп, яркость, подсветка сегментов и прозрачность сетки помогают читать форму. **Прозрачность** ослабляет весь предпросмотр для работы по референсу. Освещение следует режиму вьюпорта **MatCap / Studio / Flat**; **Smooth + Sharp** управляет сглаживанием поверхности. В Wireframe поверхность автоматически становится полупрозрачной, направляющие остаются читаемыми. Пока предпросмотр виден, штатная непрозрачная поверхность каркаса скрыта. Библиотека цветовых схем — [themes.json](themes.json).

**Допуск глубины точек** во «Внешнем виде» оставляет слегка прикрытые точки видимыми; глубоко скрытые точки остаются невидимыми.

Если включён штатный оверлей **Retopology**, в Edit Mode в **Дополнительно** появляется **Убрать заливку Retopo**. Кнопка отключает заливку каркаса в текущем вьюпорте, сохраняя остальные оверлеи. Эта настройка хранится в .blend независимо от RetopoFlow.

**Live** управляет обновлением поверхности, кнопка с глазом — её видимостью. Предпросмотр и преобразование в Mesh используют одинаковый расчёт длины кривых и рядов, в том числе во время движения точек. Сворачиваемый раздел **Хоткеи** содержит крупные кнопки для мыши или планшета.

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
| **Alt+V** | Добавить один сегмент из цепочки рёбер или ответвление из точки |
| **K** | Штатный нож Blender по кривому контуру; точки разреза следуют Live-поверхности |
| **F / X** | Стандартные Fill / Delete Blender; также доступны в разделе «Хоткеи» |
| **Shift+колесо** | Ширина |
| **Ctrl+Shift+колесо** | Детализация |
| **Ctrl+колесо** | Выпуклость |
| **Alt+колесо** | Сглаживание с шагом **0.1** |

</details>

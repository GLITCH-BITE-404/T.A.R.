import QtQuick
import Quickshell

// A side tab that comes OUT of T.A.R.'s window instead of squeezing inside
// it: a popup attached to the panel's left or right edge. If there's no room
// on that side of the screen it flips to the other side; it slides to stay
// on screen vertically. The drawer inside keeps its own open/close animation.
PopupWindow {
    id: pop
    property Item anchorItem
    property bool leftSide: true
    property bool shown: false              // drawer open
    property real gap: 10
    property real topInset: 6
    default property alias content: holder.data

    anchor.item: anchorItem
    anchor.rect.x: leftSide ? -gap : (anchorItem ? anchorItem.width + gap : 0)
    anchor.rect.y: topInset
    anchor.rect.width: 1
    anchor.rect.height: 1
    anchor.edges: (leftSide ? Edges.Left : Edges.Right) | Edges.Top
    anchor.gravity: (leftSide ? Edges.Left : Edges.Right) | Edges.Bottom
    anchor.adjustment: PopupAdjustment.FlipX | PopupAdjustment.SlideY | PopupAdjustment.ResizeY

    color: "transparent"
    grabFocus: false
    // stay mapped until the drawer's close animation has finished
    visible: shown || closing.running
    onShownChanged: if (!shown) closing.restart()
    Timer { id: closing; interval: 360 }
    // the compositor dismisses popups when you click into another window --
    // if the tab is still meant to be open, put it straight back
    onVisibleChanged: if (!visible && shown) reshow.restart()
    Timer { id: reshow; interval: 120; onTriggered: if (pop.shown && !pop.visible) pop.visible = true }

    Item { id: holder; anchors.fill: parent }
}

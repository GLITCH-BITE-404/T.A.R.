import QtQuick
import Quickshell
import Quickshell.Io

// T.A.R. background-tasks tab -- docks to the right edge whenever a task is
// running. Collapsed it's a small handle (arrow + pulse); expanded it shows
// the task, how long it has run, time left, the round, what it last did, and
// PAUSE/RESUME + KILL. Reads loop.json (written by the task itself) once a
// second -- one `cat`, nothing heavier.
Item {
    id: tasks

    property var theme
    property color accent: "#cba6f7"
    property var scaleFn: null
    function s(v) { return scaleFn ? scaleFn(v) : v }

    property var task: null             // parsed loop.json, or null
    property bool open: true
    property real now: Date.now() / 1000

    readonly property bool active: task !== null
    readonly property string dataDir: Quickshell.env("TAR_DATA")
        || (Quickshell.env("HOME") + "/.local/share/bite-os/tar")
    readonly property string tools: Quickshell.env("HOME")
        + "/.config/hypr/scripts/quickshell/tar/tar_tools.py"

    width: open ? s(300) : s(30)
    height: open ? col.implicitHeight + s(28) : s(120)
    visible: active
    opacity: active ? 1 : 0
    Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Behavior on height { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    Behavior on opacity { NumberAnimation { duration: 200 } }

    // ---------------------------------------------------------------- data
    Process {
        id: reader
        command: ["cat", tasks.dataDir + "/loop.json"]
        stdout: StdioCollector {
            onStreamFinished: {
                var t = (this.text || "").trim();
                if (t === "") { tasks.task = null; return; }
                try { tasks.task = JSON.parse(t); } catch (e) { /* mid-write: keep last */ }
            }
        }
        onExited: (code) => { if (code !== 0) tasks.task = null; }
    }
    Timer {
        interval: 1000; repeat: true; running: true; triggeredOnStart: true
        onTriggered: { tasks.now = Date.now() / 1000; if (!reader.running) reader.running = true; }
    }
    Process { id: ctl; property var argv: []; command: argv }
    function control(action) {
        ctl.argv = ["python3", tasks.tools, "run", action];
        ctl.running = true;
        reader.running = true;
    }

    function fmt(sec) {
        sec = Math.max(0, Math.floor(sec));
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), ss = sec % 60;
        return (h > 0 ? h + "h " : "") + (h > 0 || m > 0 ? m + "m " : "") + ss + "s";
    }
    readonly property bool paused: active && !!task.paused
    readonly property string status: !active ? "" :
        (paused ? "paused"
         : (now - (task.beat || now) > 120 ? "not responding" : (task.status || "running")))
    readonly property real elapsed: active
        ? (paused ? (task.paused_at || now) : now) - (task.started || now) : 0
    readonly property real timeLeft: active && task.until
        ? (task.until - (paused ? (task.paused_at || now) : now)) : -1

    // ---------------------------------------------------------------- chrome
    Rectangle {
        anchors.fill: parent
        color: Qt.rgba(tasks.theme.crust.r, tasks.theme.crust.g, tasks.theme.crust.b, 0.93)
        border.width: 1
        border.color: Qt.rgba(tasks.accent.r, tasks.accent.g, tasks.accent.b, 0.55)
    }

    // handle: arrow to fold/unfold
    Item {
        id: handle
        width: tasks.s(30)
        height: parent.height
        anchors.left: parent.left

        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            y: tasks.s(10)
            text: tasks.open ? "▶" : "◀"
            color: tasks.accent
            font.pixelSize: tasks.s(11)
        }
        Rectangle {
            id: dot
            width: tasks.s(8); height: width; radius: width / 2
            anchors.horizontalCenter: parent.horizontalCenter
            y: tasks.s(34)
            color: tasks.paused ? tasks.theme.yellow
                 : tasks.status === "not responding" || tasks.status === "failed"
                   ? tasks.theme.red : tasks.theme.green
            SequentialAnimation on opacity {
                loops: Animation.Infinite
                running: tasks.active && !tasks.paused
                NumberAnimation { to: 0.25; duration: 650 }
                NumberAnimation { to: 1.0; duration: 650 }
            }
        }
        Text {
            visible: !tasks.open
            anchors.horizontalCenter: parent.horizontalCenter
            y: tasks.s(52)
            text: tasks.active ? "R" + (tasks.task.round || 0) : ""
            color: tasks.theme.subtext0
            font.family: "JetBrains Mono"
            font.pixelSize: tasks.s(9)
        }
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: tasks.open = !tasks.open
        }
    }

    // ---------------------------------------------------------------- body
    Column {
        id: col
        visible: tasks.open
        anchors.left: handle.right
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: tasks.s(12)
        anchors.leftMargin: tasks.s(4)
        spacing: tasks.s(7)

        Text {
            text: "◢ BACKGROUND TASK"
            color: tasks.accent
            font.family: "JetBrains Mono"
            font.pixelSize: tasks.s(10)
            font.bold: true
            font.letterSpacing: tasks.s(1.5)
        }
        Text {
            width: col.width
            text: tasks.active ? tasks.task.task : ""
            color: tasks.theme.text
            font.family: "JetBrains Mono"
            font.pixelSize: tasks.s(10)
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
        Grid {
            columns: 2
            columnSpacing: tasks.s(10)
            rowSpacing: tasks.s(3)
            Repeater {
                model: [
                    ["status", tasks.status],
                    ["round", tasks.active ? String(tasks.task.round || 0) : ""],
                    ["running", tasks.fmt(tasks.elapsed)],
                    ["left", tasks.timeLeft >= 0 ? tasks.fmt(tasks.timeLeft) : "no limit"],
                    ["next", tasks.active && tasks.task.status === "waiting" && !tasks.paused
                             ? "in " + tasks.fmt((tasks.task.next_at || tasks.now) - tasks.now) : "—"]
                ]
                delegate: Item {
                    required property var modelData
                    width: lbl.width + val.width + tasks.s(8)
                    height: lbl.height
                    Text {
                        id: lbl
                        width: tasks.s(58)
                        text: modelData[0]
                        color: tasks.theme.overlay0
                        font.family: "JetBrains Mono"
                        font.pixelSize: tasks.s(9)
                    }
                    Text {
                        id: val
                        anchors.left: lbl.right
                        text: modelData[1]
                        color: modelData[0] === "status" && tasks.paused ? tasks.theme.yellow
                             : tasks.theme.subtext1
                        font.family: "JetBrains Mono"
                        font.pixelSize: tasks.s(9)
                    }
                }
            }
        }
        Text {
            width: col.width
            visible: tasks.active && (tasks.task.last || "") !== ""
            text: tasks.active ? "last: " + tasks.task.last : ""
            color: tasks.theme.overlay1
            font.family: "JetBrains Mono"
            font.pixelSize: tasks.s(8)
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
        Row {
            spacing: tasks.s(7)
            TarConsoleChip {
                theme: tasks.theme; accent: tasks.accent; scaleFn: tasks.scaleFn
                text: tasks.paused ? "▶ RESUME" : "❚❚ PAUSE"
                active: tasks.paused
                onTapped: tasks.control(tasks.paused ? "resume_task" : "pause_task")
            }
            TarConsoleChip {
                theme: tasks.theme; accent: tasks.theme.red; scaleFn: tasks.scaleFn
                text: "✕ KILL"
                onTapped: tasks.control("stop_task")
            }
        }
    }
}

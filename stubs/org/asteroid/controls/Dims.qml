pragma Singleton
import QtQuick 2.9
// wfmix PC-Stub: Dims.l(x) = x Prozent der Displaybreite (Testfenster 640 px)
QtObject {
    function l(x) { return x * 6.4 }
    function w(x) { return x * 6.4 }
    function h(x) { return x * 6.4 }
}

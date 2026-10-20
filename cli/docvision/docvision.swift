// docvision — reconocimiento estructurado de documentos con Vision (macOS 26+).
//
// Uso: docvision <imagen> [<imagen> ...]
// Salida (stdout): un JSON por ejecución:
//   {"pages":[{"path":..., "blocks":[
//      {"type":"title|paragraph|table|list", "x":..,"y":..,"w":..,"h":..,  // 0-1, origen arriba-izquierda
//       "text":"...", "rows":[["celda",...]], "items":[{"marker":"1.","text":".."}]}]}]}
// Errores por imagen: {"path":..., "error":"..."}; el proceso sale con 0 mientras pueda emitir JSON.
import Foundation
import Vision
import CoreGraphics

struct Box { var x = 0.0, y = 0.0, w = 0.0, h = 0.0 }

func box(_ region: ContoursObservation.Contour) -> Box {
    let pts = region.points
    guard !pts.isEmpty else { return Box() }
    let xs = pts.map { Double($0.x) }, ys = pts.map { Double($0.y) }
    // Vision: origen abajo-izquierda → convertimos a arriba-izquierda.
    return Box(x: xs.min()!, y: 1 - ys.max()!, w: xs.max()! - xs.min()!, h: ys.max()! - ys.min()!)
}

func block(_ type: String, _ b: Box, _ extra: [String: Any]) -> [String: Any] {
    var d: [String: Any] = ["type": type, "x": b.x, "y": b.y, "w": b.w, "h": b.h]
    for (k, v) in extra { d[k] = v }
    return d
}

func cellText(_ c: DocumentObservation.Container) -> String {
    c.text.transcript.replacingOccurrences(of: "\n", with: " ").trimmingCharacters(in: .whitespaces)
}

@available(macOS 26.0, *)
func analyze(_ path: String) async throws -> [String: Any] {
    var request = RecognizeDocumentsRequest()
    request.textRecognitionOptions.automaticallyDetectLanguage = true
    request.textRecognitionOptions.useLanguageCorrection = true
    request.barcodeDetectionOptions.enabled = false
    let observations = try await request.perform(on: URL(fileURLWithPath: path))
    var blocks: [[String: Any]] = []
    for obs in observations {
        let doc = obs.document
        // Regiones de tablas y listas: el texto dentro de ellas no se repite como párrafo.
        let tableBoxes = doc.tables.map { box($0.boundingRegion) }
        let listBoxes = doc.lists.map { box($0.boundingRegion) }
        func inside(_ b: Box, _ outer: [Box]) -> Bool {
            let cx = b.x + b.w / 2, cy = b.y + b.h / 2
            return outer.contains { cx >= $0.x && cx <= $0.x + $0.w && cy >= $0.y && cy <= $0.y + $0.h }
        }
        for t in doc.tables {
            var grid: [[String]] = []
            for (r, row) in t.rows.enumerated() {
                if grid.count <= r { grid.append([]) }
                for cell in row {
                    let c = cell.columnRange.lowerBound
                    while grid[r].count <= c { grid[r].append("") }
                    grid[r][c] = cellText(cell.content)
                }
            }
            let width = grid.map { $0.count }.max() ?? 0
            grid = grid.map { $0 + Array(repeating: "", count: width - $0.count) }
            blocks.append(block("table", box(t.boundingRegion), ["rows": grid]))
        }
        for l in doc.lists {
            let items = l.items.map { ["marker": $0.markerString, "text": $0.itemString] }
            blocks.append(block("list", box(l.boundingRegion), ["items": items]))
        }
        let titleText = doc.title?.transcript
        for p in doc.paragraphs {
            let b = box(p.boundingRegion)
            if inside(b, tableBoxes) || inside(b, listBoxes) { continue }
            let text = p.transcript.trimmingCharacters(in: .whitespacesAndNewlines)
            if text.isEmpty { continue }
            blocks.append(block(text == titleText ? "title" : "paragraph", b, ["text": text]))
        }
    }
    // Orden de lectura: de arriba abajo; a igual altura (±1.5 %), de izquierda a derecha.
    blocks.sort {
        let ya = $0["y"] as! Double, yb = $1["y"] as! Double
        if abs(ya - yb) > 0.015 { return ya < yb }
        return ($0["x"] as! Double) < ($1["x"] as! Double)
    }
    return ["path": path, "blocks": blocks]
}

@main
struct Main {
    static func main() async {
        let paths = Array(CommandLine.arguments.dropFirst())
        guard #available(macOS 26.0, *) else {
            FileHandle.standardError.write(Data("docvision requiere macOS 26 o posterior\n".utf8))
            exit(2)
        }
        var pages: [[String: Any]] = []
        for p in paths {
            do { pages.append(try await analyze(p)) }
            catch { pages.append(["path": p, "error": "\(error)"]) }
        }
        let data = try! JSONSerialization.data(withJSONObject: ["pages": pages], options: [.sortedKeys])
        FileHandle.standardOutput.write(data)
    }
}

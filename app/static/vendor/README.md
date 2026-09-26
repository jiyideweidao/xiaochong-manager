# vendor/

为了离线可用（程序完全不出网），这两个文件是本地内置的，不通过 CDN 加载。

| 文件 | 出处 | 版本 | 许可 |
| --- | --- | --- | --- |
| `three.min.js` | [three.js](https://threejs.org/) 构建产物 | r147 | MIT（文件头保留原始 license 声明） |
| `OrbitControls.js` | three.js `examples/js/controls`（非 ESM 版） | r147 | MIT |

升级方式：从 three.js 官方发行包取同名文件替换即可，代码里只用到
`WebGLRenderer` / `PerspectiveCamera` / `EdgesGeometry` / `LineSegments` / `Mesh` 与 `OrbitControls`。

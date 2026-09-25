# ZINE連携コンテンツ公開システム

ZINEに印刷するURLと、実際に公開するファイルの対応を管理します。

## 基本ルール

- QRコードには `https://t-kosuke.com/r/ランダムID/` を登録する。
- 一度発行したランダムIDとURLは変更・再利用しない。
- 実ファイルを差し替える場合は、新しいランダムなファイル名で追加する。
- 差し替え後も `public/r/ランダムID/index.html` は同じURLで維持する。
- `resources.json`、公開ファイル、QRコードを一緒にGitで管理する。

## 配置場所

- `public/r/`: QRコードから開く固定URLの入口
- `public/media/`: 画像、動画、音声、PDFなど
- `public/works/`: HTML作品と、その関連ファイル
- `content-registry/resources.json`: ID、URL、実ファイルの管理用対応表
- `qr-codes/`: 印刷・確認用のQRコード

## QRコード

QRコードはPNGとSVGの2形式で保存します。通常の確認にはPNG、印刷物への配置には拡大しても劣化しないSVGが適しています。

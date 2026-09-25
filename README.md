# シンプル静的サイト

Astro を取り除き、`public/` 配下の静的ファイルだけで構成した極小サイトです。

## ディレクトリ

```text
/
├── public/
│   ├── favicon.ico
│   ├── favicon.svg
│   └── index.html
└── README.md
```

## 表示方法

1. `public/index.html` をブラウザで直接開く。
2. もしくは任意の静的ファイルサーバーで公開する。
   - 例: `cd public && python3 -m http.server 8080`

## デプロイ

`public` ディレクトリの中身をそのままホスティングサービスにアップロードしてください。ビルドや依存パッケージは不要です。

## ZINE連携コンテンツ

ZINEに印刷したQRコードから、独自ドメイン上の画像やHTML作品を表示できます。

- 公開URL: `https://t-kosuke.com/r/ランダムID/`
- 対応表: `content-registry/resources.json`
- 公開ファイル: `public/media/` および `public/works/`
- 印刷用QRコード: `qr-codes/`

詳しい運用ルールは `content-registry/README.md` を参照してください。

### 画像の簡単登録

1. `登録待ち` フォルダへJPEG、PNG、WebP画像を入れる。
2. `ZINEコンテンツ登録.cmd` をダブルクリックする。
3. 確認画面に従って登録する。

初回だけ、画像処理とQRコード生成に必要な準備が自動的に行われます。

発行済みのQRコードは、ローカル専用の `QRコード一覧.html` で検索・確認できます。画像サムネイルと、標準PNG・小型PNG・SVGへのリンクも表示します。

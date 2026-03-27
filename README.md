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

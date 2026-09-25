from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

from PIL import Image, ImageOps
import qrcode
from qrcode.constants import ERROR_CORRECT_M
from qrcode.image.svg import SvgPathImage
import zxingcpp


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "登録待ち"
REGISTRY_PATH = ROOT / "content-registry" / "resources.json"
PUBLIC_MEDIA = ROOT / "public" / "media"
PUBLIC_ROUTES = ROOT / "public" / "r"
QR_DIR = ROOT / "qr-codes"
CATALOG_PATH = ROOT / "QRコード一覧.html"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}
PUBLIC_ID_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
VARIANT_WIDTHS = (960, 1600)


class RegistrationError(RuntimeError):
    pass


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_registry() -> dict:
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RegistrationError(f"対応表を読み込めませんでした: {exc}") from exc


def write_registry(registry: dict) -> None:
    temporary = REGISTRY_PATH.with_suffix(".json.tmp")
    temporary.write_text(
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, REGISTRY_PATH)


def catalog_html(registry: dict) -> str:
    type_labels = {
        "image": "画像",
        "html": "HTML",
        "video": "動画",
        "audio": "音声",
        "pdf": "PDF",
    }
    resources = sorted(
        registry.get("resources", {}).items(),
        key=lambda item: (item[1].get("created", ""), item[0]),
        reverse=True,
    )
    cards: list[str] = []
    for public_id, resource in resources:
        resource_type = str(resource.get("type", "other"))
        type_label = type_labels.get(resource_type, resource_type.upper())
        title = str(resource.get("title", public_id))
        created = str(resource.get("created", ""))
        url = str(resource.get("url", ""))
        qr_base = f"{public_id}-{resource_type}"
        escaped_title = html.escape(title, quote=True)
        escaped_url = html.escape(url, quote=True)
        variants = resource.get("variants", [])
        if resource_type == "image" and variants:
            thumbnail_path = html.escape(str(variants[0]["file"]), quote=True)
            preview = f'''<a class="thumbnail-link" href="{escaped_url}" target="_blank" rel="noopener noreferrer">
          <img class="thumbnail" src="{thumbnail_path}" alt="{escaped_title}のサムネイル" loading="lazy">
        </a>'''
        else:
            preview = f'''<div class="thumbnail-placeholder" aria-label="{html.escape(type_label)}コンテンツ">
          <span>{html.escape(type_label)}</span>
        </div>'''
        cards.append(
            f'''    <article class="card" data-title="{escaped_title}" data-id="{public_id}" data-type="{resource_type}" data-created="{created}">
      <div class="previews">
        <a class="qr-link" href="qr-codes/{qr_base}.png" target="_blank">
          <img class="qr" src="qr-codes/{qr_base}.png" alt="{escaped_title}のQRコード" loading="lazy">
        </a>
        {preview}
      </div>
      <div class="content">
        <div class="meta"><span>{html.escape(type_label)}</span><time datetime="{created}">{created}</time></div>
        <h2>{escaped_title}</h2>
        <p class="id">ID: {public_id}</p>
        <p class="url">{escaped_url}</p>
        <div class="actions">
          <a class="primary" href="{escaped_url}" target="_blank" rel="noopener noreferrer">公開ページを開く</a>
          <button type="button" data-copy="{escaped_url}">URLをコピー</button>
          <a href="qr-codes/{qr_base}.png" target="_blank">標準PNG</a>
          <a href="qr-codes/{qr_base}-small.png" target="_blank">小型PNG</a>
          <a href="qr-codes/{qr_base}.svg" target="_blank">SVG</a>
        </div>
      </div>
    </article>'''
        )

    card_markup = "\n".join(cards)
    total = len(resources)
    return f'''<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>発行済みQRコード一覧</title>
  <style>
    :root {{
      color-scheme: light;
      --paper: #f4f3ef;
      --surface: #ffffff;
      --ink: #1c211e;
      --subtle: #66706a;
      --line: #d6dcd8;
      --accent: #17634c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      background: var(--paper);
      color: var(--ink);
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
      line-height: 1.5;
    }}
    header {{
      padding: 32px max(20px, calc((100vw - 1180px) / 2));
      background: var(--surface);
      border-bottom: 1px solid var(--line);
    }}
    h1 {{ margin: 0 0 6px; font-size: clamp(24px, 4vw, 36px); }}
    header p {{ margin: 0; color: var(--subtle); }}
    .controls {{
      max-width: 1180px;
      margin: 24px auto 0;
      padding: 0 20px;
      display: grid;
      grid-template-columns: minmax(220px, 1fr) auto auto;
      gap: 10px;
    }}
    input, select {{
      width: 100%;
      min-height: 44px;
      padding: 9px 12px;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: var(--surface);
      color: var(--ink);
      font: inherit;
    }}
    .summary {{
      max-width: 1180px;
      margin: 14px auto;
      padding: 0 20px;
      color: var(--subtle);
      font-size: 14px;
    }}
    .grid {{
      max-width: 1180px;
      margin: 0 auto 48px;
      padding: 0 20px;
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(340px, 1fr));
      gap: 16px;
    }}
    .card {{
      padding: 16px;
      background: var(--surface);
      border: 1px solid var(--line);
      border-radius: 10px;
    }}
    .card[hidden] {{ display: none; }}
    .previews {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin-bottom: 14px; }}
    .qr-link, .thumbnail-link, .thumbnail-placeholder {{
      display: block;
      overflow: hidden;
      aspect-ratio: 1;
      border: 1px solid var(--line);
      border-radius: 7px;
      background: #fff;
    }}
    .qr {{ display: block; width: 100%; height: auto; aspect-ratio: 1; }}
    .thumbnail {{ display: block; width: 100%; height: 100%; object-fit: cover; }}
    .thumbnail-placeholder {{ display: grid; place-items: center; color: var(--subtle); background: #eef1ef; font-weight: 600; }}
    .content {{ min-width: 0; }}
    .meta {{ display: flex; justify-content: space-between; gap: 12px; color: var(--subtle); font-size: 12px; }}
    h2 {{ margin: 8px 0 3px; font-size: 17px; line-height: 1.35; }}
    .id {{ margin: 0; color: var(--subtle); font: 12px ui-monospace, monospace; }}
    .url {{ margin: 10px 0; overflow-wrap: anywhere; font: 12px ui-monospace, monospace; }}
    .actions {{ display: flex; flex-wrap: wrap; gap: 6px; }}
    .actions a, .actions button {{
      min-height: 34px;
      padding: 7px 9px;
      border: 1px solid var(--line);
      border-radius: 6px;
      background: var(--surface);
      color: var(--ink);
      font: inherit;
      font-size: 12px;
      text-decoration: none;
      cursor: pointer;
    }}
    .actions .primary {{ background: var(--accent); border-color: var(--accent); color: white; }}
    .empty {{ display: none; max-width: 1180px; margin: 30px auto; padding: 0 20px; color: var(--subtle); }}
    @media (max-width: 720px) {{
      .controls {{ grid-template-columns: 1fr; }}
    }}
  </style>
</head>
<body>
  <header>
    <h1>発行済みQRコード一覧</h1>
    <p>このファイルは管理用です。公開サイトには配置されません。</p>
  </header>
  <section class="controls" aria-label="絞り込みと並べ替え">
    <input id="search" type="search" placeholder="タイトルまたはIDで検索" aria-label="検索">
    <select id="type" aria-label="種類">
      <option value="">すべての種類</option>
      <option value="image">画像</option>
      <option value="html">HTML</option>
      <option value="video">動画</option>
      <option value="audio">音声</option>
      <option value="pdf">PDF</option>
    </select>
    <select id="sort" aria-label="並べ替え">
      <option value="newest">新しい順</option>
      <option value="oldest">古い順</option>
      <option value="title">タイトル順</option>
    </select>
  </section>
  <p class="summary"><span id="visible-count">{total}</span> / {total}件を表示</p>
  <main id="grid" class="grid">
{card_markup}
  </main>
  <p id="empty" class="empty">条件に合うQRコードはありません。</p>
  <script>
    const grid = document.getElementById("grid");
    const cards = Array.from(grid.querySelectorAll(".card"));
    const search = document.getElementById("search");
    const type = document.getElementById("type");
    const sort = document.getElementById("sort");
    const visibleCount = document.getElementById("visible-count");
    const empty = document.getElementById("empty");

    function refresh() {{
      const query = search.value.trim().toLocaleLowerCase("ja");
      const selectedType = type.value;
      const ordered = [...cards].sort((a, b) => {{
        if (sort.value === "oldest") return a.dataset.created.localeCompare(b.dataset.created);
        if (sort.value === "title") return a.dataset.title.localeCompare(b.dataset.title, "ja");
        return b.dataset.created.localeCompare(a.dataset.created);
      }});
      let count = 0;
      ordered.forEach((card) => {{
        const text = `${{card.dataset.title}} ${{card.dataset.id}}`.toLocaleLowerCase("ja");
        const visible = (!query || text.includes(query)) && (!selectedType || card.dataset.type === selectedType);
        card.hidden = !visible;
        if (visible) count += 1;
        grid.appendChild(card);
      }});
      visibleCount.textContent = count;
      empty.style.display = count ? "none" : "block";
    }}

    async function copyUrl(button) {{
      const value = button.dataset.copy;
      try {{
        await navigator.clipboard.writeText(value);
      }} catch (_) {{
        const field = document.createElement("textarea");
        field.value = value;
        document.body.appendChild(field);
        field.select();
        document.execCommand("copy");
        field.remove();
      }}
      const original = button.textContent;
      button.textContent = "コピーしました";
      setTimeout(() => {{ button.textContent = original; }}, 1400);
    }}

    grid.addEventListener("click", (event) => {{
      const button = event.target.closest("button[data-copy]");
      if (button) copyUrl(button);
    }});
    search.addEventListener("input", refresh);
    type.addEventListener("change", refresh);
    sort.addEventListener("change", refresh);
    refresh();
  </script>
</body>
</html>
'''


def write_catalog(registry: dict) -> None:
    temporary = CATALOG_PATH.with_suffix(".html.tmp")
    temporary.write_text(catalog_html(registry), encoding="utf-8")
    os.replace(temporary, CATALOG_PATH)


def git(*arguments: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    process = subprocess.run(
        ["git", *arguments],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if check and process.returncode != 0:
        detail = process.stderr.strip() or process.stdout.strip()
        raise RegistrationError(f"Gitの処理に失敗しました: {detail}")
    return process


def ensure_clean_repository() -> None:
    status = git("status", "--porcelain").stdout.strip()
    if status:
        raise RegistrationError(
            "サイト編集フォルダに未記録の変更があります。"
            "別の編集作業を完了してから、もう一度実行してください。"
        )


def pull_latest() -> None:
    git("pull", "--ff-only", "origin", "main")


def find_images() -> list[Path]:
    INBOX.mkdir(parents=True, exist_ok=True)
    return sorted(
        path
        for path in INBOX.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def existing_source_hashes(registry: dict) -> set[str]:
    return {
        resource["sourceSha256"]
        for resource in registry.get("resources", {}).values()
        if resource.get("sourceSha256")
    }


def new_public_id(registry: dict) -> str:
    while True:
        candidate = "".join(secrets.choice(PUBLIC_ID_ALPHABET) for _ in range(12))
        if (
            candidate not in registry.get("resources", {})
            and not (PUBLIC_ROUTES / candidate).exists()
        ):
            return candidate


def new_file_name(extension: str) -> str:
    return f"{secrets.token_hex(16)}{extension}"


def default_title(path: Path) -> str:
    stem = path.stem.strip()
    if not stem or stem.upper().startswith(("IMG_", "DSC_")):
        return "ZINE連携画像"
    return stem.replace("_", " ").replace("-", " ")


def ask_confirmation(files: list[Path]) -> bool:
    import tkinter as tk
    from tkinter import messagebox

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    names = "\n".join(f"・{path.name}" for path in files)
    answer = messagebox.askyesno(
        "ZINEコンテンツ登録",
        f"次の画像を公開します。\n\n{names}\n\n処理を開始しますか。",
        parent=root,
    )
    root.destroy()
    return answer


def ask_titles(files: list[Path]) -> dict[Path, str] | None:
    import tkinter as tk
    from tkinter import simpledialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    titles: dict[Path, str] = {}
    for path in files:
        title = simpledialog.askstring(
            "画像タイトル",
            f"「{path.name}」のタイトルを入力してください。\n"
            "そのままでよければ変更せずOKを押します。",
            initialvalue=default_title(path),
            parent=root,
        )
        if title is None:
            root.destroy()
            return None
        titles[path] = title.strip() or default_title(path)
    root.destroy()
    return titles


def show_message(kind: str, title: str, message: str) -> None:
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        root.attributes("-topmost", True)
        getattr(messagebox, kind)(title, message, parent=root)
        root.destroy()
    except Exception:
        print(message)


def optimize_image(source: Path, output_dir: Path) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    try:
        with Image.open(source) as opened:
            oriented = ImageOps.exif_transpose(opened)
            if "A" in oriented.getbands():
                rgba = oriented.convert("RGBA")
                background = Image.new("RGBA", rgba.size, "white")
                background.alpha_composite(rgba)
                image = background.convert("RGB")
            else:
                image = oriented.convert("RGB")
    except Exception as exc:
        raise RegistrationError(f"画像を開けませんでした: {source.name}: {exc}") from exc

    widths = sorted({min(width, image.width) for width in VARIANT_WIDTHS})
    variants: list[dict] = []
    for width in widths:
        height = round(image.height * width / image.width)
        resized = image.resize((width, height), Image.Resampling.LANCZOS)
        file_name = new_file_name(".webp")
        destination = output_dir / file_name
        resized.save(destination, "WEBP", quality=82, method=6)
        variants.append(
            {
                "width": width,
                "height": height,
                "format": "webp",
                "fileName": file_name,
                "size": destination.stat().st_size,
            }
        )
    return variants


def viewer_html(title: str, variants: list[dict]) -> str:
    escaped_title = html.escape(title, quote=True)
    smallest = variants[0]
    largest = variants[-1]
    srcset = ",\n          ".join(
        f"../../media/{variant['fileName']} {variant['width']}w"
        for variant in variants
    )
    return f'''<!doctype html>
<html lang="ja">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="robots" content="noindex, nofollow">
  <title>{escaped_title}</title>
  <style>
    :root {{ color-scheme: light; }}
    * {{ box-sizing: border-box; }}
    body {{
      min-height: 100vh;
      margin: 0;
      display: grid;
      place-items: center;
      padding: 16px;
      background: #f4f4f2;
      color: #171817;
      font-family: system-ui, -apple-system, BlinkMacSystemFont, "Hiragino Sans", sans-serif;
    }}
    main {{ width: min(100%, 960px); }}
    figure {{ margin: 0; }}
    img {{
      display: block;
      width: 100%;
      height: auto;
      max-height: calc(100vh - 32px);
      object-fit: contain;
    }}
  </style>
</head>
<body>
  <main>
    <figure>
      <img
        src="../../media/{smallest['fileName']}"
        srcset="
          {srcset}
        "
        sizes="(max-width: 992px) calc(100vw - 32px), 960px"
        width="{largest['width']}"
        height="{largest['height']}"
        decoding="async"
        fetchpriority="high"
        alt="{escaped_title}"
      >
    </figure>
  </main>
</body>
</html>
'''


def make_qr(url: str, box_size: int) -> qrcode.QRCode:
    qr = qrcode.QRCode(
        error_correction=ERROR_CORRECT_M,
        box_size=box_size,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    return qr


def verify_qr(path: Path, expected_url: str) -> None:
    decoded = zxingcpp.read_barcode(Image.open(path))
    if decoded is None or decoded.text != expected_url:
        raise RegistrationError(f"生成したQRコードの読取検証に失敗しました: {path.name}")


def create_qr(
    url: str,
    png_path: Path,
    small_png_path: Path,
    svg_path: Path,
) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    qr = make_qr(url, box_size=20)
    qr.make_image(fill_color="black", back_color="white").save(png_path)
    qr.make_image(image_factory=SvgPathImage).save(svg_path)
    small_qr = make_qr(url, box_size=6)
    small_qr.make_image(fill_color="black", back_color="white").save(small_png_path)
    verify_qr(png_path, url)
    verify_qr(small_png_path, url)


def ensure_small_qr_codes(registry: dict) -> list[Path]:
    QR_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    for public_id, resource in registry.get("resources", {}).items():
        resource_type = str(resource.get("type", "other"))
        destination = QR_DIR / f"{public_id}-{resource_type}-small.png"
        if destination.exists():
            verify_qr(destination, str(resource["url"]))
            continue
        small_qr = make_qr(str(resource["url"]), box_size=6)
        small_qr.make_image(fill_color="black", back_color="white").save(destination)
        verify_qr(destination, str(resource["url"]))
        created.append(destination)
    return created


def stage_resource(
    source: Path,
    title: str,
    source_hash: str,
    registry: dict,
    staging: Path,
) -> tuple[str, dict, list[Path]]:
    public_id = new_public_id(registry)
    route_url = f"{registry['baseUrl']}{public_id}/"
    media_stage = staging / "public" / "media"
    route_stage = staging / "public" / "r" / public_id
    qr_stage = staging / "qr-codes"

    variants = optimize_image(source, media_stage)
    route_stage.mkdir(parents=True, exist_ok=True)
    (route_stage / "index.html").write_text(
        viewer_html(title, variants), encoding="utf-8"
    )

    qr_png_name = f"{public_id}-image.png"
    qr_small_png_name = f"{public_id}-image-small.png"
    qr_svg_name = f"{public_id}-image.svg"
    create_qr(
        route_url,
        qr_stage / qr_png_name,
        qr_stage / qr_small_png_name,
        qr_stage / qr_svg_name,
    )

    stored_variants = [
        {
            "width": variant["width"],
            "height": variant["height"],
            "format": variant["format"],
            "file": f"public/media/{variant['fileName']}",
        }
        for variant in variants
    ]
    resource = {
        "type": "image",
        "title": title,
        "sourceName": source.name,
        "sourceSha256": source_hash,
        "target": stored_variants[-1]["file"],
        "variants": stored_variants,
        "url": route_url,
        "created": time.strftime("%Y-%m-%d"),
        "status": "published",
    }
    generated = [
        Path(variant["file"]) for variant in stored_variants
    ] + [
        Path("public") / "r" / public_id / "index.html",
        Path("qr-codes") / qr_png_name,
        Path("qr-codes") / qr_small_png_name,
        Path("qr-codes") / qr_svg_name,
    ]
    return public_id, resource, generated


def publish_staging(staging: Path, generated: list[Path]) -> None:
    for relative in generated:
        source = staging / relative
        destination = ROOT / relative
        if destination.exists():
            raise RegistrationError(f"出力先がすでに存在します: {relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))


def commit_and_push(paths: list[Path], count: int) -> None:
    relative_paths = [path.as_posix() for path in paths]
    relative_paths.append("content-registry/resources.json")
    git("add", "--", *relative_paths)
    staged = git("diff", "--cached", "--name-only").stdout.strip()
    if not staged:
        raise RegistrationError("Gitへ記録するファイルが見つかりませんでした。")
    git("commit", "-m", f"ZINE画像を{count}件登録")
    git("push", "origin", "main")


def wait_for_publication(resource: dict, timeout_seconds: int = 60) -> bool:
    expected = Path(resource["variants"][0]["file"]).name
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            request = urllib.request.Request(
                f"{resource['url']}?check={int(time.time())}",
                headers={"User-Agent": "ZINE content registration tool"},
            )
            with urllib.request.urlopen(request, timeout=10) as response:
                body = response.read().decode("utf-8", errors="replace")
                if response.status == 200 and expected in body:
                    return True
        except (OSError, urllib.error.URLError):
            pass
        time.sleep(5)
    return False


def open_catalog() -> None:
    if sys.platform == "win32":
        os.startfile(CATALOG_PATH)  # type: ignore[attr-defined]


def register_images(assume_yes: bool = False) -> int:
    files = find_images()
    if not files:
        show_message(
            "showinfo",
            "ZINEコンテンツ登録",
            "「登録待ち」フォルダに新しい画像がありません。",
        )
        return 0

    registry = load_registry()
    known_hashes = existing_source_hashes(registry)
    candidates: list[tuple[Path, str]] = []
    duplicates: list[Path] = []
    for path in files:
        source_hash = file_sha256(path)
        if source_hash in known_hashes:
            duplicates.append(path)
        else:
            candidates.append((path, source_hash))

    if not candidates:
        names = "\n".join(f"・{path.name}" for path in duplicates)
        show_message(
            "showinfo",
            "ZINEコンテンツ登録",
            f"すべて登録済みです。二重登録は行いませんでした。\n\n{names}",
        )
        return 0

    candidate_files = [path for path, _ in candidates]
    if not assume_yes and not ask_confirmation(candidate_files):
        return 0
    if assume_yes:
        titles = {path: default_title(path) for path in candidate_files}
    else:
        titles = ask_titles(candidate_files)
        if titles is None:
            return 0

    ensure_clean_repository()
    pull_latest()
    ensure_clean_repository()
    registry = load_registry()
    known_hashes = existing_source_hashes(registry)
    for _, source_hash in candidates:
        if source_hash in known_hashes:
            raise RegistrationError(
                "別の端末で同じ画像が登録されました。もう一度実行してください。"
            )

    staging = Path(tempfile.mkdtemp(prefix=".zine-register-", dir=ROOT))
    generated_paths: list[Path] = []
    added_resources: list[dict] = []
    try:
        for source, source_hash in candidates:
            public_id, resource, generated = stage_resource(
                source,
                titles[source],
                source_hash,
                registry,
                staging,
            )
            registry["resources"][public_id] = resource
            generated_paths.extend(generated)
            added_resources.append(resource)

        publish_staging(staging, generated_paths)
        write_registry(registry)
        write_catalog(registry)
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    commit_and_push(generated_paths, len(added_resources))
    publication_results = [wait_for_publication(item) for item in added_resources]
    urls = "\n".join(item["url"] for item in added_resources)
    if all(publication_results):
        message = (
            f"{len(added_resources)}件を公開しました。\n\n{urls}\n\n"
            "QRコード一覧を開きます。"
        )
    else:
        message = (
            f"{len(added_resources)}件をGitHubへ送りました。\n\n{urls}\n\n"
            "公開側の反映確認が時間内に終わりませんでした。少し待ってからURLを確認してください。"
        )
    show_message("showinfo", "ZINEコンテンツ登録 完了", message)
    open_catalog()
    return 0


def self_test() -> int:
    with tempfile.TemporaryDirectory(prefix="zine-tool-test-") as temporary:
        work = Path(temporary)
        source = work / "test image.png"
        sample = Image.new("RGBA", (1200, 900), (92, 150, 196, 160))
        sample.save(source, "PNG")
        registry = {
            "version": 1,
            "baseUrl": "https://t-kosuke.com/r/",
            "resources": {},
        }
        public_id, resource, generated = stage_resource(
            source,
            "テスト画像",
            file_sha256(source),
            registry,
            work,
        )
        assert resource["url"] == f"https://t-kosuke.com/r/{public_id}/"
        assert [item["width"] for item in resource["variants"]] == [960, 1200]
        assert all((work / path).exists() for path in generated)
        page = (work / "public" / "r" / public_id / "index.html").read_text(
            encoding="utf-8"
        )
        assert all(Path(item["file"]).name in page for item in resource["variants"])
        assert all(
            len(Image.open(path).getexif()) == 0
            for path in (work / "public" / "media").glob("*.webp")
        )
        registry["resources"][public_id] = resource
        catalog = catalog_html(registry)
        assert "発行済みQRコード一覧" in catalog
        assert resource["url"] in catalog
        assert f"qr-codes/{public_id}-image.png" in catalog
        assert f"qr-codes/{public_id}-image-small.png" in catalog
        assert resource["variants"][0]["file"] in catalog
    print("Self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ZINE連携画像の登録ツール")
    parser.add_argument("--yes", action="store_true", help="確認画面を省略する")
    parser.add_argument("--self-test", action="store_true", help="自己診断を実行する")
    parser.add_argument(
        "--build-catalog", action="store_true", help="QRコード一覧HTMLを更新する"
    )
    arguments = parser.parse_args()
    if arguments.self_test:
        return self_test()
    if arguments.build_catalog:
        registry = load_registry()
        created = ensure_small_qr_codes(registry)
        write_catalog(registry)
        print(f"Small QR codes created: {len(created)}")
        print(f"Catalog updated: {CATALOG_PATH}")
        return 0
    try:
        return register_images(assume_yes=arguments.yes)
    except RegistrationError as exc:
        show_message("showerror", "ZINEコンテンツ登録 エラー", str(exc))
        print(str(exc), file=sys.stderr)
        return 1
    except Exception as exc:
        show_message(
            "showerror",
            "ZINEコンテンツ登録 エラー",
            f"予期しない問題が発生しました。\n{exc}",
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())

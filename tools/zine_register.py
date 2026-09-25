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
from qrcode.constants import ERROR_CORRECT_H
from qrcode.image.svg import SvgPathImage
import zxingcpp


ROOT = Path(__file__).resolve().parents[1]
INBOX = ROOT / "登録待ち"
REGISTRY_PATH = ROOT / "content-registry" / "resources.json"
PUBLIC_MEDIA = ROOT / "public" / "media"
PUBLIC_ROUTES = ROOT / "public" / "r"
QR_DIR = ROOT / "qr-codes"
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


def create_qr(url: str, png_path: Path, svg_path: Path) -> None:
    png_path.parent.mkdir(parents=True, exist_ok=True)
    qr = qrcode.QRCode(
        error_correction=ERROR_CORRECT_H,
        box_size=20,
        border=4,
    )
    qr.add_data(url)
    qr.make(fit=True)
    qr.make_image(fill_color="black", back_color="white").save(png_path)
    qr.make_image(image_factory=SvgPathImage).save(svg_path)
    decoded = zxingcpp.read_barcode(Image.open(png_path))
    if decoded is None or decoded.text != url:
        raise RegistrationError("生成したQRコードの読取検証に失敗しました。")


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
    qr_svg_name = f"{public_id}-image.svg"
    create_qr(route_url, qr_stage / qr_png_name, qr_stage / qr_svg_name)

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


def open_qr_folder() -> None:
    if sys.platform == "win32":
        os.startfile(QR_DIR)  # type: ignore[attr-defined]


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
    finally:
        shutil.rmtree(staging, ignore_errors=True)

    commit_and_push(generated_paths, len(added_resources))
    publication_results = [wait_for_publication(item) for item in added_resources]
    urls = "\n".join(item["url"] for item in added_resources)
    if all(publication_results):
        message = (
            f"{len(added_resources)}件を公開しました。\n\n{urls}\n\n"
            "QRコードのフォルダを開きます。"
        )
    else:
        message = (
            f"{len(added_resources)}件をGitHubへ送りました。\n\n{urls}\n\n"
            "公開側の反映確認が時間内に終わりませんでした。少し待ってからURLを確認してください。"
        )
    show_message("showinfo", "ZINEコンテンツ登録 完了", message)
    open_qr_folder()
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
    print("Self-test passed")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="ZINE連携画像の登録ツール")
    parser.add_argument("--yes", action="store_true", help="確認画面を省略する")
    parser.add_argument("--self-test", action="store_true", help="自己診断を実行する")
    arguments = parser.parse_args()
    if arguments.self_test:
        return self_test()
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

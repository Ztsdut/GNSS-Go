#!/usr/bin/env python3
from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.pdfmetrics import registerFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, Image, KeepTogether, PageBreak, PageTemplate,
    Paragraph, Spacer, Table, TableStyle
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
IMAGES = DOCS / "images"
IMAGES.mkdir(parents=True, exist_ok=True)
ZH_SCREEN = IMAGES / "gui_overview_zh.png"
EN_SCREEN = IMAGES / "gui_overview_en.png"
ICON = ROOT / "src" / "gnssgo" / "gui" / "resources" / "icons" / "gnss_go.png"

# The current release screenshots are stored under docs/images.
# Replace these PNG files before rebuilding manuals when the GUI changes.
if not ZH_SCREEN.exists() or not EN_SCREEN.exists():
    raise SystemExit("Missing docs/images/gui_overview_zh.png or gui_overview_en.png")

registerFont(UnicodeCIDFont("STSong-Light"))

BLUE = colors.HexColor("#246FD5")
NAVY = colors.HexColor("#16335B")
LIGHT = colors.HexColor("#F3F7FC")
BORDER = colors.HexColor("#D7E3F2")
GRAY = colors.HexColor("#64748B")
GREEN = colors.HexColor("#169B62")


def latin_runs(text: str, *, bold: bool = False) -> str:
    """Chinese body in Song; ASCII letter/digit runs in Times family."""
    escaped = html.escape(text)
    font = "Times-Bold" if bold else "Times-Roman"
    # Keep markup-free text only; ASCII runs include common CLI punctuation.
    return re.sub(
        r"([A-Za-z0-9][A-Za-z0-9_./:+()\[\]{}=<>@#%&*,'\"\\\- ]*)",
        lambda m: f'<font name="{font}">{m.group(1)}</font>',
        escaped,
    )


def build_styles(lang: str):
    if lang == "zh":
        body_font = "STSong-Light"
        title_font = "STSong-Light"
        h_font = "STSong-Light"
    else:
        body_font = "Times-Roman"
        title_font = "Times-Bold"
        h_font = "Times-Bold"
    return {
        "title": ParagraphStyle("title", fontName=title_font, fontSize=25, leading=34, textColor=NAVY, alignment=TA_CENTER, spaceAfter=10),
        "subtitle": ParagraphStyle("subtitle", fontName=body_font, fontSize=12, leading=18, textColor=GRAY, alignment=TA_CENTER, spaceAfter=12),
        "h1": ParagraphStyle("h1", fontName=h_font, fontSize=17, leading=24, textColor=BLUE, spaceBefore=4, spaceAfter=10),
        "h2": ParagraphStyle("h2", fontName=h_font, fontSize=13.5, leading=20, textColor=NAVY, spaceBefore=8, spaceAfter=6),
        "body": ParagraphStyle("body", fontName=body_font, fontSize=10.5, leading=17, textColor=colors.HexColor("#26384A"), spaceAfter=6),
        "bullet": ParagraphStyle("bullet", fontName=body_font, fontSize=10.2, leading=16, leftIndent=12, firstLineIndent=-7, spaceAfter=3),
        "code": ParagraphStyle("code", fontName="Courier", fontSize=8.6, leading=13, textColor=colors.HexColor("#18324A"), backColor=colors.HexColor("#EEF4FA"), borderColor=BORDER, borderWidth=0.5, borderPadding=6, spaceBefore=4, spaceAfter=8),
        "caption": ParagraphStyle("caption", fontName=body_font, fontSize=8.8, leading=13, textColor=GRAY, alignment=TA_CENTER, spaceBefore=4, spaceAfter=10),
        "small": ParagraphStyle("small", fontName=body_font, fontSize=8.5, leading=13, textColor=GRAY),
    }


def P(text: str, style, lang: str, *, bold_latin=False):
    return Paragraph(latin_runs(text, bold=bold_latin) if lang == "zh" else html.escape(text), style)


def Pmarkup(text: str, style, lang: str):
    # For intentionally marked-up strings; Chinese callers should already use latin_runs where needed.
    return Paragraph(text, style)


def bullet(text: str, styles, lang: str):
    prefix = "• "
    return P(prefix + text, styles["bullet"], lang)


def code(text: str, styles):
    return Paragraph(html.escape(text).replace("\n", "<br/>"), styles["code"])


def fit_image(path: Path, max_w: float, max_h: float):
    from PIL import Image as PILImage
    with PILImage.open(path) as im:
        w, h = im.size
    scale = min(max_w / w, max_h / h)
    return Image(str(path), width=w * scale, height=h * scale)


def page_decor(canvas, doc, lang: str):
    canvas.saveState()
    canvas.setStrokeColor(BORDER)
    canvas.setLineWidth(0.5)
    canvas.line(18*mm, 14*mm, A4[0]-18*mm, 14*mm)
    canvas.setFillColor(GRAY)
    font = "STSong-Light" if lang == "zh" else "Times-Roman"
    canvas.setFont(font, 8)
    left = "GNSS Go 用户手册" if lang == "zh" else "GNSS Go User Manual"
    canvas.drawString(18*mm, 9*mm, left)
    canvas.drawRightString(A4[0]-18*mm, 9*mm, str(doc.page))
    canvas.restoreState()


def doc_template(path: Path, lang: str):
    doc = BaseDocTemplate(str(path), pagesize=A4, leftMargin=18*mm, rightMargin=18*mm, topMargin=18*mm, bottomMargin=20*mm,
                          title="GNSS Go User Manual", author="GNSS Go")
    frame = Frame(doc.leftMargin, doc.bottomMargin, doc.width, doc.height, id="normal")
    doc.addPageTemplates(PageTemplate(id="main", frames=[frame], onPage=lambda c,d: page_decor(c,d,lang)))
    return doc


def section_header(story, text, styles, lang):
    story.append(P(text, styles["h1"], lang, bold_latin=True))


def subsection(story, text, styles, lang):
    story.append(P(text, styles["h2"], lang, bold_latin=True))


def info_table(rows, styles, lang, widths=None):
    data=[]
    for row in rows:
        data.append([P(str(cell), styles["body"], lang) for cell in row])
    tbl=Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), LIGHT),
        ("TEXTCOLOR", (0,0), (-1,0), NAVY),
        ("GRID", (0,0), (-1,-1), 0.4, BORDER),
        ("VALIGN", (0,0), (-1,-1), "TOP"),
        ("LEFTPADDING", (0,0), (-1,-1), 6),
        ("RIGHTPADDING", (0,0), (-1,-1), 6),
        ("TOPPADDING", (0,0), (-1,-1), 5),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
    ]))
    return tbl


def build_zh():
    styles=build_styles("zh")
    path=DOCS/"GNSS-Go_User_Manual_ZH.pdf"
    doc=doc_template(path,"zh")
    s=[]
    s.append(Spacer(1,18*mm))
    if ICON.exists(): s.append(fit_image(ICON, 36*mm,36*mm)); s.append(Spacer(1,8*mm))
    s.append(P("GNSS Go 用户手册", styles["title"], "zh", bold_latin=True))
    s.append(P("版本 0.1.2", styles["subtitle"], "zh"))
    s.append(P("全球与区域 GNSS / CORS 测站浏览、下载计划与数据获取", styles["subtitle"], "zh"))
    s.append(Spacer(1,10*mm))
    s.append(P("本手册对应当前 GNSS Go 桌面界面与命令行工具。软件启动时优先加载随安装包提供的测站位置快照，并在后台静默更新在线测站目录。", styles["body"], "zh"))
    s.append(PageBreak())

    section_header(s,"1. 软件简介",styles,"zh")
    s += [P("GNSS Go 是面向 GNSS 数据用户的桌面与命令行工具，用于在统一界面中浏览全球 IGS 与多个国家/区域 CORS 网络，设置日期、采样率和 RINEX 版本，生成下载计划，并执行自动或半自动数据获取。",styles["body"],"zh"),
          bullet("支持全球 IGS 与亚洲、欧洲、美洲、大洋洲等区域数据源。",styles,"zh"),
          bullet("联网时优先使用 OpenStreetMap，离线时使用内置底图。",styles,"zh"),
          bullet("安装包内置测站位置快照，首次启动即可看到大量测站；在线目录随后在后台静默刷新。",styles,"zh"),
          bullet("支持观测数据、导航电文和精密产品的计划与下载。",styles,"zh"),
          bullet("日本 GEONET 使用浏览器自动化流程；韩国国家 GNSS 数据中心使用网页会话创建临时 ZIP 后直接下载。",styles,"zh")]

    subsection(s,"1.1 数据源图标",styles,"zh")
    s.append(info_table([
        ["图标","含义"],
        ["✅","GNSS Go 内可直接完成下载，包括 HTTP/FTP/SFTP 或软件内部浏览器自动化。"],
        ["🌐","软件提供测站目录和官方入口，但需要用户在官方数据源完成登录或下载。"],
    ],styles,"zh",[28*mm,140*mm]))

    section_header(s,"2. Windows 安装与启动",styles,"zh")
    s.append(P("普通用户建议使用 GitHub Releases 中的 Windows 安装程序。开发或测试源码时，可在 PowerShell 中执行：",styles["body"],"zh"))
    s.append(code('python -m venv .venv\n.\\.venv\\Scripts\\Activate.ps1\npython -m pip install --upgrade pip\npython -m pip install -e ".[build,hatanaka,unix-z]"\ngnssgo gui',styles))
    s.append(P("如果需要在本机生成独立 GUI 与命令行可执行程序：",styles["body"],"zh"))
    s.append(code('python packaging\\build.py --clean --gui --cli',styles))
    s.append(P("生成的 dist\\GNSS-Go.exe 为桌面程序，dist\\gnssgo.exe 为命令行程序。发布安装包前建议先直接运行两个 EXE 验证。",styles["body"],"zh"))

    section_header(s,"3. 主界面",styles,"zh")
    if ZH_SCREEN.exists():
        s.append(fit_image(ZH_SCREEN, doc.width, 112*mm))
        s.append(P("图 1  GNSS Go 中文主界面（用户提供的当前版本截图）",styles["caption"],"zh"))
    s.append(P("界面从左到右依次为主导航、区域/数据源树、测站地图与数据可用性面板、当前选择与下载参数。",styles["body"],"zh"))
    s.append(info_table([
        ["区域","功能"],
        ["左侧导航","首页、观测数据、导航数据、产品、下载管理和设置。"],
        ["区域/数据源","勾选 IGS、洲、国家/地区或区域网络；数字表示当前目录中的测站数。"],
        ["测站地图","选择单站、框选或半径选择；蓝色为 IGS，橙色为区域 CORS。"],
        ["数据可用性","完整展开所有已选数据源，不省略“另外 N 个”条目。"],
        ["当前选择","设置起止日期、Provider、采样率、RINEX 和输出目录。"],
    ],styles,"zh",[36*mm,132*mm]))

    section_header(s,"4. 启动、地图与测站目录",styles,"zh")
    subsection(s,"4.1 首次启动",styles,"zh")
    s.append(P("GNSS Go 在创建主窗口前读取内置测站位置快照，因此不依赖在线站点接口完成首屏绘制。联网时会在后台静默刷新 IGS、EPN、GEONET、GeoNet 等已接入目录，刷新结果与本地目录合并，不会先清空地图。",styles["body"],"zh"))
    subsection(s,"4.2 OpenStreetMap",styles,"zh")
    s.append(P("启动时会做一次短时网络探测；如果 OpenStreetMap 可访问，地图默认使用 OpenStreetMap；否则自动回退到离线地图。可在地图工具栏手动切换。",styles["body"],"zh"))
    subsection(s,"4.3 欧洲测站",styles,"zh")
    s.append(P("欧洲 EPN 测站位置已加入安装包内置快照。即使首次启动时 EPN 在线目录暂时无法访问，欧洲仍会显示随版本发布的 EPN 测站；联网后软件会静默刷新更完整的在线目录。",styles["body"],"zh"))

    section_header(s,"5. 观测数据下载",styles,"zh")
    s.append(P("推荐按以下顺序操作：",styles["body"],"zh"))
    for x in ["在区域/数据源树选择 IGS、国家/地区或区域网络。","在地图点击测站，或使用框选/半径选择多个站。","在右侧选择起止日期；日期输入格式为 YYYY-MM-DD。","Provider 建议先用 auto；根据数据源选择采样率和 RINEX 版本。","点击“计划 (PLAN)”检查远端文件数、待下载、已存在和不可用组合。","确认计划后执行下载，在“下载管理”查看进度和结果。"]:
        s.append(bullet(x,styles,"zh"))
    subsection(s,"5.1 PLAN 与下载的区别",styles,"zh")
    s.append(P("PLAN 用于发现与核对文件，不应提前创建一次性下载任务。对于韩国 GNSSData，临时 ZIP 的 key 只在真正下载时生成并立即使用。",styles["body"],"zh"))

    section_header(s,"6. 重点区域数据源",styles,"zh")
    rows=[
        ["区域/网络","软件内处理方式"],
        ["IGS","自动选择可用公共镜像并下载。"],
        ["欧洲 EPN","内置 EPN 测站快照；在线时刷新 EPN 目录，文件发现后按可用镜像回退。"],
        ["日本 GEONET","软件打开 Terras，自动选择站点、日期、GRJE / RINEX 3，并执行一括下载。"],
        ["韩国国家 GNSS 数据中心","同一网页 Session 中创建 ZIP，取得临时 key 后立即下载。"],
        ["中国台湾 GDMS","英文显示 Taiwan, China；软件提供 GNSS 测站目录和官方入口，下载需遵守官网登录要求。"],
        ["中国香港 SatRef","英文显示 Hong Kong, China；内置 SatRef 测站位置并提供对应数据源。"],
        ["加拿大","提供 NRCan CACS / UNB CHAIN 等已接入网络。"],
    ]
    s.append(info_table(rows,styles,"zh",[47*mm,121*mm]))

    section_header(s,"7. 导航数据、产品与下载管理",styles,"zh")
    subsection(s,"7.1 导航数据",styles,"zh")
    s.append(P("“导航数据”页面用于下载广播星历/导航电文。设置日期和导航类型后生成 PLAN，再执行下载。",styles["body"],"zh"))
    subsection(s,"7.2 产品",styles,"zh")
    s.append(P("“产品”页面用于精密轨道、钟差、ERP、IONEX、ANTEX、SINEX 等已接入产品。自动模式会按产品类型、时间和已配置公共数据源选择候选文件。",styles["body"],"zh"))
    subsection(s,"7.3 下载管理",styles,"zh")
    s.append(P("“下载管理”显示任务状态、文件数、字节进度、失败原因和输出位置。遇到部分失败时，先查看具体 Provider 与文件错误，再决定是否重试。",styles["body"],"zh"))

    section_header(s,"8. 设置",styles,"zh")
    s += [bullet("语言：中文 / English，可即时切换。",styles,"zh"),
          bullet("地图：联网时默认 OpenStreetMap；离线地图无需网络。",styles,"zh"),
          bullet("代理：支持 Direct、System、HTTP 和 SOCKS5；可分别应用于 HTTP、FTP、SFTP。",styles,"zh"),
          bullet("ChromeDriver：日本 GEONET、智利 CSN 等浏览器型流程需要兼容的 Chrome/ChromeDriver。",styles,"zh"),
          bullet("下载：可设置并发数、重试、断点续传、自动解压和保留压缩文件。",styles,"zh")]

    section_header(s,"9. 命令行",styles,"zh")
    s.append(code('gnssgo --help\ngnssgo doctor\ngnssgo gui',styles))
    s.append(P("CLI 与 GUI 使用同一套配置、Provider 和下载核心。对于需要人工网页交互的来源，GUI 通常更方便。",styles["body"],"zh"))

    section_header(s,"10. 常见问题",styles,"zh")
    qa=[
        ["问题","处理方法"],
        ["地图刚启动没有底图","检查网络或代理；OpenStreetMap 不可达时切换离线地图。"],
        ["欧洲没有橙色测站","0.1.2 起已内置 EPN 测站快照；若旧缓存仍异常，更新到新版本后重新启动。"],
        ["IGS 数量稍后增加","属于后台静默刷新结果；首屏已使用安装包快照，不影响立即操作。"],
        ["日本下载失败","确认 Chrome/ChromeDriver 可用，并查看错误停在哪个 GEONET 阶段。"],
        ["韩国旧 getZip 链接失效","临时 key 为下载流程的一部分，必须由软件创建后立即使用，不要重复打开旧链接。"],
        ["PyInstaller 不存在","在激活的 .venv 中重新运行 python -m pip install -e \".[build,hatanaka,unix-z]\"。"],
    ]
    s.append(info_table(qa,styles,"zh",[46*mm,122*mm]))

    section_header(s,"11. 数据与许可说明",styles,"zh")
    s.append(P("GNSS Go 是独立的数据访问工具。第三方 GNSS/CORS 数据的版权、许可、引用要求、账户要求和访问规则由原始提供机构决定。使用数据前请查看对应官方数据源的规定。软件仓库本身按 LICENSE 文件声明的许可发布。",styles["body"],"zh"))
    s.append(P("建议公开发布前先在 Windows、macOS 和 Linux 的 GitHub Actions 原生运行器上完成构建与基本启动测试。",styles["body"],"zh"))
    doc.build(s)
    return path


def build_en():
    styles=build_styles("en")
    path=DOCS/"GNSS-Go_User_Manual_EN.pdf"
    doc=doc_template(path,"en")
    s=[]
    s.append(Spacer(1,18*mm))
    if ICON.exists(): s.append(fit_image(ICON,36*mm,36*mm)); s.append(Spacer(1,8*mm))
    s.append(P("GNSS Go User Manual",styles["title"],"en"))
    s.append(P("Version 0.1.2",styles["subtitle"],"en"))
    s.append(P("Global and regional GNSS/CORS station discovery, planning and data access",styles["subtitle"],"en"))
    s.append(Spacer(1,10*mm))
    s.append(P("This manual describes the current GNSS Go desktop interface and command-line tools. The application loads a bundled station-position snapshot before the first map view and refreshes supported online catalogs silently in the background.",styles["body"],"en"))
    s.append(PageBreak())

    section_header(s,"1. Overview",styles,"en")
    s.append(P("GNSS Go provides one interface for browsing global IGS and regional CORS networks, selecting stations and dates, reviewing a download plan, and retrieving supported GNSS files.",styles["body"],"en"))
    for x in ["Global IGS and regional networks across Asia, Europe, the Americas and Oceania.","OpenStreetMap is preferred when reachable; an offline map is available without network access.","A bundled station snapshot makes the first map useful immediately; online catalogs update silently afterwards.","Observation, navigation and precise-product planning/downloading.","Provider-specific workflows for Japan GEONET and Korea GNSSData."]:
        s.append(bullet(x,styles,"en"))
    subsection(s,"1.1 Access badges",styles,"en")
    s.append(info_table([["Badge","Meaning"],["✅","Download can be completed inside GNSS Go, including HTTP/FTP/SFTP or internal browser automation."],["🌐","The station catalog and official source are provided, but the user must complete the provider's own login/download workflow."]],styles,"en",[28*mm,140*mm]))

    section_header(s,"2. Windows installation and launch",styles,"en")
    s.append(P("For end users, install the Windows package from GitHub Releases. For source development/testing, open PowerShell in the repository root:",styles["body"],"en"))
    s.append(code('python -m venv .venv\n.\\.venv\\Scripts\\Activate.ps1\npython -m pip install --upgrade pip\npython -m pip install -e ".[build,hatanaka,unix-z]"\ngnssgo gui',styles))
    s.append(P("To build standalone GUI and CLI executables locally:",styles["body"],"en"))
    s.append(code('python packaging\\build.py --clean --gui --cli',styles))
    s.append(P("The output is dist\\GNSS-Go.exe for the desktop application and dist\\gnssgo.exe for the CLI.",styles["body"],"en"))

    section_header(s,"3. Main interface",styles,"en")
    if EN_SCREEN.exists():
        s.append(fit_image(EN_SCREEN,doc.width,112*mm))
        s.append(P("Figure 1. Current GNSS Go English interface (user-supplied screenshot).",styles["caption"],"en"))
    s.append(P("The window is organized into the main navigation bar, the region/data-source tree, the station map and availability panel, and the current selection/download configuration panel.",styles["body"],"en"))
    s.append(info_table([["Area","Purpose"],["Main navigation","Home, Observations, Navigation, Products, Downloads and Settings."],["Region / Data Source","Select IGS, continents, countries/regions and integrated networks. Counts reflect the local catalog."],["Station map","Click individual stations or use rectangle/radius selection. IGS and regional stations use different marker colors."],["Data availability","Shows every selected source; entries are not collapsed into an 'and N more' line."],["Current Selection","Configure dates, provider, sampling, RINEX version and output folder."]],styles,"en",[38*mm,130*mm]))

    section_header(s,"4. Startup, maps and station catalogs",styles,"en")
    subsection(s,"4.1 Immediate station display",styles,"en")
    s.append(P("GNSS Go reads the bundled station-position snapshot before constructing the main map. The first view therefore does not depend on live station APIs. When online, supported IGS and regional catalogs refresh silently and merge into the existing catalog without clearing the map first.",styles["body"],"en"))
    subsection(s,"4.2 OpenStreetMap",styles,"en")
    s.append(P("A short connectivity probe runs at startup. If OpenStreetMap is reachable, it is selected as the default basemap; otherwise the application falls back to the bundled offline map.",styles["body"],"en"))
    subsection(s,"4.3 Europe",styles,"en")
    s.append(P("GNSS Go 0.1.2 includes a bundled EPN coordinate fallback so European stations remain visible even when the live EPN catalog is temporarily unavailable. A background EPN refresh can then extend or update the release snapshot.",styles["body"],"en"))

    section_header(s,"5. Observation workflow",styles,"en")
    for x in ["Select IGS, a country/region, or a regional network in the left tree.","Select one or more stations on the map, or use rectangle/radius tools.","Choose the start and end date in YYYY-MM-DD format.","Use Provider = auto unless a specific source is required; choose sampling and RINEX as needed.","Click Plan (PLAN) to review remote files, files already present, files to download and unavailable combinations.","Start the download and monitor progress in Downloads."]:
        s.append(bullet(x,styles,"en"))
    subsection(s,"5.1 PLAN versus download",styles,"en")
    s.append(P("Planning discovers and validates candidate files. One-time server-side download jobs should only be created during the actual download. For Korea GNSSData, GNSS Go creates the temporary ZIP key and consumes it immediately in the same web session.",styles["body"],"en"))

    section_header(s,"6. Important regional sources",styles,"en")
    s.append(info_table([["Region / network","GNSS Go behavior"],["IGS","Automatic public-mirror selection and download."],["Europe / EPN","Bundled EPN station snapshot plus silent live refresh; file discovery uses configured EPN mirrors/fallbacks."],["Japan / GEONET","Terras browser automation selects stations/dates, GRJE and RINEX 3, then triggers bulk download."],["Korea / National GNSS Data Center","Creates a temporary ZIP in the same public web session and downloads it immediately using the returned key."],["Taiwan, China / GDMS","The English UI uses 'Taiwan, China'. GNSS station metadata and the official portal are provided; provider login rules still apply."],["Hong Kong, China / SatRef","The English UI uses 'Hong Kong, China'; SatRef station coordinates are bundled."],["Canada","Integrated sources include NRCan CACS and UNB CHAIN."],],styles,"en",[51*mm,117*mm]))

    section_header(s,"7. Navigation, Products and Downloads",styles,"en")
    subsection(s,"7.1 Navigation",styles,"en")
    s.append(P("Use Navigation for broadcast ephemeris/navigation files. Configure the date and navigation type, review the plan, then download.",styles["body"],"en"))
    subsection(s,"7.2 Products",styles,"en")
    s.append(P("Products covers integrated precise orbit, clock, ERP, IONEX, ANTEX, SINEX and related files. Automatic mode resolves candidates by requested product type, date and configured public providers.",styles["body"],"en"))
    subsection(s,"7.3 Downloads",styles,"en")
    s.append(P("Downloads shows task state, file progress, byte progress, failures and output paths. For partial results, inspect the failed provider/file details before retrying.",styles["body"],"en"))

    section_header(s,"8. Settings",styles,"en")
    for x in ["Language: English / Chinese, switchable at runtime.","Map: OpenStreetMap is preferred when online; the offline map is always available.","Proxy: Direct, System, HTTP and SOCKS5 modes can be applied to HTTP, FTP and SFTP workflows.","ChromeDriver: browser-based providers such as Japan GEONET require a compatible Chrome/ChromeDriver environment.","Download settings: concurrency, retry, resume, automatic extraction and keeping compressed files."]:
        s.append(bullet(x,styles,"en"))

    section_header(s,"9. Command line",styles,"en")
    s.append(code('gnssgo --help\ngnssgo doctor\ngnssgo gui',styles))
    s.append(P("The CLI and GUI share the same configuration, providers and download core. Browser-interactive sources are usually easier to operate from the GUI.",styles["body"],"en"))

    section_header(s,"10. Troubleshooting",styles,"en")
    s.append(info_table([["Issue","Action"],["No online basemap at startup","Check network/proxy settings or switch to the offline map."],["No orange stations in Europe","Version 0.1.2 bundles EPN coordinates. Upgrade and restart if an older cache/release is still in use."],["IGS count increases after startup","This is the result of silent background catalog refresh; the bundled snapshot is already usable immediately."],["Japan download fails","Verify Chrome/ChromeDriver and use the stage-specific GEONET error message to identify where the workflow stopped."],["An old Korea getZip link fails","The temporary key is generated for the live download workflow and should be consumed immediately, not reused later."],["PyInstaller is missing","Activate .venv and run python -m pip install -e \".[build,hatanaka,unix-z]\"."],],styles,"en",[49*mm,119*mm]))

    section_header(s,"11. Data and licensing",styles,"en")
    s.append(P("GNSS Go is an independent data-access client. Copyright, licenses, citation requirements, account requirements and access rules for third-party GNSS/CORS data remain with the original providers. Review the official provider terms before using downloaded data. The GNSS Go repository itself is distributed under the license stated in LICENSE.",styles["body"],"en"))
    s.append(P("For public releases, build and smoke-test the application on native Windows, macOS and Linux runners through GitHub Actions.",styles["body"],"en"))
    doc.build(s)
    return path


def write_sources():
    zh='''# GNSS Go 用户手册（中文）\n\n版本：0.1.2\n\n本手册的 PDF 版本为 `GNSS-Go_User_Manual_ZH.pdf`，主界面图片使用当前中文界面截图。\n\n## 主要章节\n\n1. 软件简介\n2. Windows 安装与启动\n3. 主界面\n4. 启动、地图与测站目录\n5. 观测数据下载\n6. 重点区域数据源\n7. 导航数据、产品与下载管理\n8. 设置\n9. 命令行\n10. 常见问题\n11. 数据与许可说明\n\nWindows 源码安装/打包依赖：\n\n```powershell\npython -m pip install -e ".[build,hatanaka,unix-z]"\n```\n\n英文界面中中国台湾显示为 `Taiwan, China`，中国香港显示为 `Hong Kong, China`。欧洲 EPN 测站位置随版本快照提供，并在联网后后台静默刷新。\n'''
    en='''# GNSS Go User Manual (English)\n\nVersion: 0.1.2\n\nThe PDF edition is `GNSS-Go_User_Manual_EN.pdf` and uses the current English GUI screenshot supplied for this release.\n\n## Sections\n\n1. Overview\n2. Windows installation and launch\n3. Main interface\n4. Startup, maps and station catalogs\n5. Observation workflow\n6. Important regional sources\n7. Navigation, Products and Downloads\n8. Settings\n9. Command line\n10. Troubleshooting\n11. Data and licensing\n\nWindows source/build dependencies:\n\n```powershell\npython -m pip install -e ".[build,hatanaka,unix-z]"\n```\n\nThe English UI uses `Taiwan, China` and `Hong Kong, China`. European EPN coordinates are bundled in the release snapshot and refreshed silently when online.\n'''
    (DOCS/'user_manual_zh.md').write_text(zh,encoding='utf-8')
    (DOCS/'user_manual_en.md').write_text(en,encoding='utf-8')

if __name__=='__main__':
    write_sources()
    print(build_zh())
    print(build_en())

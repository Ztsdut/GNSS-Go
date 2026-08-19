from __future__ import annotations

from gnssgo.gui.qt import require_qt

QtCore, _QtGui, _QtWidgets = require_qt()


_ZH = {
    'Home': '首页',
    'Observations': '观测数据',
    'Navigation': '导航数据',
    'Products': '精密产品',
    'Downloads': '下载管理',
    'Settings': '设置',
    'Download Navigation': '下载导航数据',
    'Download Products': '下载精密产品',
    'Product Request': '产品请求',
    'Product Summary': '产品摘要',
    'Download queue': '下载队列',
    'Plan progress': '计划进度',
    'Regional Sources': '区域数据来源',
    'European Networks': '欧洲网络',
    'Canada Sources': '加拿大数据来源',
    'No sources': '未选择来源',
    'Each queue row shows whole-plan progress; the current file is shown separately.': (
        '下载队列中的进度条表示整个下载计划；当前文件进度单独显示。'
    ),
    'Output folder': '输出目录',
    'Download completed': '下载完成',
    'Download partially completed': '下载部分完成',
    'Download failed': '下载失败',
    'Selected download': '所选下载',
    'Activity log': '活动日志',
    'Active': '进行中',
    'Completed': '已完成',
    'Failed / Partial': '失败 / 部分完成',
    'Start': '开始',
    'End': '结束',
    'Provider': '数据源',
    'Output': '输出目录',
    'Browse…': '浏览…',
    'Review Plan': '查看下载计划',
    'Save': '保存',
    'Cancel': '取消',
    'Retry Failed': '重试失败项',
    'Open folder': '打开文件夹',
    'Search': '搜索',
    'Select All': '全选',
    'Theme': '主题',
    'Language': '语言',
    'System': '系统',
    'Tier': '产品等级',
    'Analysis Center': '分析中心',
    'Quick setup': '快速配置',
    'Product resolutions': '产品时间分辨率',
    'Temporal resolution': '时间分辨率',
    'Resolution / interval': '时间分辨率',
    'Product': '产品',
    'Current file': '当前文件',
    'No download selected': '未选择下载任务',
    'Details': '详情',
    'Files': '个文件',
    'Data root': '数据根目录',
    'Station catalog': '测站目录',
    'Workers': '并发任务数',
    'Per-provider workers': '单数据源并发数',
    'Retries': '重试次数',
    'Resume': '断点续传',
    'Automatically decompress': '下载后自动解压',
    'Keep compressed': '解压后保留原压缩文件',
    'Proxy': '代理',
    'General': '常规',
    'Download': '下载',
    'Providers': '数据源',
    'Network': '网络',
    'Global OBS / NAV mirrors': '全球 OBS / NAV 镜像',
    'Product mirrors': '产品镜像',
    'Regional data sources': '区域数据源',
    'Move Up': '上移',
    'Move Down': '下移',
    'Official source ↗': '官方源 ↗',
    'Custom': '自定义',
    'PPP bundle': 'PPP 组合',
    'Ionosphere bundle': '电离层组合',
    'Orbit (SP3)': '轨道 (SP3)',
    'Clock (CLK)': '钟差 (CLK)',
    'Earth rotation (ERP)': '地球自转参数 (ERP)',
    'Bias (BIA/BSX)': '偏差 (BIA/BSX)',
    'Ionosphere (IONEX)': '电离层 (IONEX)',
    'SINEX': 'SINEX',
    'ANTEX': 'ANTEX',
    'Current / source-defined': '当前版本 / 数据源定义',
    'Automatic / source-defined': '自动 / 数据源定义',
    'Only one temporal resolution is available for the current selection.': '当前选择只有一种时间分辨率。',
    'Multiple temporal resolutions are available; choose the one you need.': (
        '当前产品有多种时间分辨率，请选择需要的时间分辨率。'
    ),
    'Pending': '等待中',
    'Planning': '正在规划',
    'Ready': '可用',
    'Downloading': '正在下载',
    'Processing': '正在处理',
    'Paused': '已暂停',
    'Partial': '部分可用',
    'Failed': '失败',
    'Cancelled': '已取消',
    'Download Observations': '下载观测数据',
    'Browse & Download Observations': '浏览并下载观测数据',
    'Quick actions': '快捷操作',
    'Station Catalog': '测站目录',
    'Data Networks': '数据网络',
    'Storage': '存储位置',
    'Navigation type': '导航数据类型',
    'Default archive location': '默认数据归档目录',
    'Data Network': '数据网络',
    'Data Network Filter': '数据网络筛选',
    'Australia Sources': '澳大利亚数据来源',
    'Station map': '测站地图',
    'Station list': '测站列表',
    'Visible stations': '可见测站',
    'Paste IDs': '粘贴站点 ID',
    'Import File': '导入文件',
    'Sampling': '采样间隔',
    'RINEX': 'RINEX',
    'Select': '选择',
    'Rectangle': '框选',
    'Radius': '半径',
    'Fit': '适应视图',
    'Clear selection': '清除选择',
    'Individual stations': '独立站点',
    'Clusters': '聚合站点',
    'Offline': '离线底图',
    'OpenStreetMap': 'OpenStreetMap',
    'Select products': '选择产品',
    'Output directory': '输出目录',
    'Select one or more product types': '请选择一个或多个产品类型',
    'Current file / message': '当前文件 / 消息',
    'Name': '名称',
    'Type': '类型',
    'Progress': '进度',
    'Status': '状态',
    'Message': '消息',
    'Region': '区域',
    'Access': '访问方式',
    'GNSS Go': 'GNSS Go', 'Direct download in the app': '软件内可直接下载',
    'English': 'English',
    'Australia': '澳大利亚',
    'Netherlands': '荷兰',
    'Italy': '意大利',
    'Mongolia': '蒙古',
    'United States': '美国',
    'New Zealand': '新西兰',
    'Europe': '欧洲',
    'Brazil': '巴西',
    'Japan': '日本',
    'Canada': '加拿大',
    'United Kingdom': '英国',
    'France': '法国',
    'Spain': '西班牙',
    'Hong Kong': '中国香港',
    'Hong Kong, China': '中国香港',
    'Argentina': '阿根廷',
    'South Africa': '南非',
    'Portugal': '葡萄牙',
    'Korea': '韩国',
    'Singapore': '新加坡',
    'North America': '北美洲',
    'Observation download': '观测数据下载',
    'Navigation download': '导航数据下载',
    'Product download': '精密产品下载',
    'Starting download…': '正在开始下载…',
    'File-count progress (remote size not available)': (
        '按文件数显示进度（远端大小不可用）'
    ),
    'Waiting for the download plan': '等待下载计划',
    'Waiting': '等待中',
    'See download progress in the queue, then open completed output folders.': (
        '直接在下载队列中查看进度，完成后可打开输出目录。'
    ),
    'Ready to download': '准备下载',
    'Only one resolution is available for the current selection.': (
        '当前选择只有一种可用分辨率。'
    ),
    'Multiple resolutions are available; choose the one you need.': (
        '当前产品有多种可用分辨率，请选择所需分辨率。'
    ),
    'FULL/LIVE': '完整/已验证',
    'PARTIAL/LIVE': '部分/已验证',
    'AUTH': '需认证',
    'WEB': '网页访问',
    'BROWSE': '浏览器访问',
    'UNVERIFIED': '未验证',
    'Open source': '打开数据源',
    'Not live verified': '尚未实测验证',
    (
        'Browse stations on one map, select targets, configure the data, '
        'then review the download plan.'
    ): (
        '在同一张地图上浏览和筛选测站，选择目标、配置数据，'
        '然后查看下载计划。'
    ),
    'Search station ID, country, network or source…': (
        '搜索测站 ID、国家、网络或来源…'
    ),
    'Filters control what is visible. Hidden stations stay selected.': (
        '筛选器只控制可见测站；隐藏的测站仍保留已选择状态。'
    ),
    'Date': '日期',
    'Adjust persistent settings, data sources, and appearance.': (
        '配置持久化设置、数据源和界面外观。'
    ),
    (
        'Provider priority controls automatic global fallback. Regional '
        'networks use their own source. Select a provider to reveal its '
        'official source link.'
    ): (
        '数据源优先级控制全球镜像的自动回退；'
        '区域网络使用各自的数据源。'
        '选择数据源可查看其官方网站。'
    ),
    (
        'Provider names below open the official network/data source page. '
        "Status reflects GNSS Go's current integration level."
    ): (
        '下表中的数据源名称可打开官方网络/数据页面；'
        '状态表示 GNSS Go 当前的接入程度。'
    ),
    (
        'Choose product types first. Each product then uses its own '
        'available resolution; when more than one resolution is available '
        'you can choose it explicitly.'
    ): (
        '先选择产品类型。每种产品使用各自可用的分辨率；'
        '若存在多个分辨率，可由用户明确选择。'
    ),
    (
        'A single available resolution is selected automatically. If a '
        'product exposes multiple resolutions, its selector becomes '
        'editable.'
    ): (
        '若只有一种可用分辨率将自动选择；'
        '若存在多个分辨率，下拉框将自动开放选择。'
    ),
    (
        'See real download progress, completed files, failures, '
        'and output locations.'
    ): (
        '查看实时下载进度、已完成文件、失败项和输出位置。'
    ),
    (
        'Each row is one download request. Progress is updated '
        'while bytes arrive.'
    ): (
        '每一行代表一个下载任务；接收到数据时会实时更新进度。'
    ),
    'Review broadcast navigation files before download.': (
        '下载前查看广播星历下载计划。'
    ),
    'Stations Map': '测站地图',
    'Click stations to select. Rectangle and Radius are explicit tools.': (
        '点击测站进行选择；也可使用矩形框选和半径选择工具。'
    ),
    'Search...': '搜索…',
}


# Coverage added for the unified Global/Regional network tree and the remaining
# user-facing strings that were still English in Chinese mode.  Keep provider
# IDs/technical protocol names unchanged, but translate controls, countries,
# status messages and explanatory text.
_ZH.update({
    'Global': '全球',
    'Regional': '区域',
    'Africa': '非洲',
    'Antarctica': '南极洲',
    'Asia': '亚洲',
    'Latin America': '拉丁美洲',
    'Oceania': '大洋洲',
    'Europe-wide': '欧洲区域网络',
    'Expand All': '全部展开',
    'Collapse All': '全部折叠',
    'Select None': '取消全选',
    'No integrated regional source yet.': '暂无已接入的区域数据源。',
    'No networks': '未选择网络',
    '{count} sources selected': '已选择 {count} 个数据源',
    'Source': '来源',
    'Stations': '测站数',
    'Merged unique stations': '合并后的唯一测站数',
    'Station catalog is loading or has not been loaded yet.': '测站目录正在加载或尚未加载。',
    'Station catalog loaded.': '测站目录已加载。',
    'Station catalog refresh failed; retry will occur automatically.': '测站目录刷新失败，将自动重试。',
    'SIRGAS / Latin America Networks': 'SIRGAS / 拉丁美洲网络',
    'Austria': '奥地利', 'Belgium': '比利时', 'Bulgaria': '保加利亚',
    'Croatia': '克罗地亚', 'Cyprus': '塞浦路斯', 'Czechia': '捷克',
    'Denmark': '丹麦', 'Estonia': '爱沙尼亚', 'Finland': '芬兰',
    'Germany': '德国', 'Greece': '希腊', 'Hungary': '匈牙利',
    'Iceland': '冰岛', 'Ireland': '爱尔兰', 'Latvia': '拉脱维亚',
    'Lithuania': '立陶宛', 'Luxembourg': '卢森堡', 'Malta': '马耳他',
    'Montenegro': '黑山', 'Norway': '挪威', 'Poland': '波兰',
    'Romania': '罗马尼亚', 'Serbia': '塞尔维亚', 'Slovakia': '斯洛伐克',
    'Slovenia': '斯洛文尼亚', 'Sweden': '瑞典', 'Switzerland': '瑞士',
    'Türkiye': '土耳其', 'Albania': '阿尔巴尼亚',
    'Bosnia and Herzegovina': '波斯尼亚和黑塞哥维那',
    'North Macedonia': '北马其顿', 'Moldova': '摩尔多瓦', 'Ukraine': '乌克兰',
    'Bolivia': '玻利维亚', 'Colombia': '哥伦比亚', 'Ecuador': '厄瓜多尔',
    'Peru': '秘鲁', 'Uruguay': '乌拉圭', 'Costa Rica': '哥斯达黎加',
    'Panama': '巴拿马', 'Mexico': '墨西哥',
    'KASI KASINet / KVN FTP': 'KASI KASINet / KVN FTP',
    'National GNSS Data Center': '韩国国家 GNSS 数据中心',
    'NOAA National CORS Network': 'NOAA 国家 CORS 网络',
    'NRCan CACS': '加拿大 NRCan CACS', 'UNB CHAIN': '加拿大 UNB CHAIN',
    'Auto-detect chromedriver.exe': '自动检测 chromedriver.exe',
    'Browse Stations': '浏览测站', 'Browse...': '浏览...',
    'Clear selected stations and selection shapes': '清除已选测站和选择范围',
    'Clear Spatial': '清除空间筛选', 'Connection test completed.': '连接测试完成。',
    'Country': '国家/地区', 'Direct (no proxy)': '直连（不使用代理）',
    'Direct mode: all protocols connect without a proxy.': '直连模式：所有协议均不使用代理。',
    'Download Plan': '下载计划', 'Find, filter, and select stations without losing hidden selections.': '查找、筛选并选择测站，隐藏的已选测站不会丢失。',
    'Fit all visible stations': '适应所有可见测站', 'HTTP proxy': 'HTTP 代理',
    'Import failed': '导入失败', 'Map: retrying Leaflet / OpenStreetMap...': '地图：正在重试 Leaflet / OpenStreetMap…',
    'No stations added': '未添加测站',
    'None of the supplied station IDs were found in the current station catalog.': '输入的测站 ID 均未在当前测站目录中找到。',
    'Off: keep the downloaded .gz/.Z archive as the final file. On: automatically decompress/restore it after download.': '关闭：保留下载的 .gz/.Z 压缩文件作为最终文件；开启：下载后自动解压/还原。',
    'Offline always works; OpenStreetMap requires network access': '离线底图始终可用；OpenStreetMap 需要网络连接',
    'Optional': '可选', 'Planning failed': '下载计划生成失败',
    'Proxy credentials are stored in the local GNSS Go settings.json file.': '代理凭据保存在本地 GNSS Go settings.json 文件中。',
    'Radius used by the Radius map tool': '半径选择工具使用的半径',
    'Search station': '搜索测站',
    'Select at least one product type before reviewing the plan.': '查看计划前请至少选择一种产品类型。',
    'Select at least one station on the map, in the station list, or by pasting/importing station IDs.': '请在地图、测站列表中至少选择一个测站，或粘贴/导入测站 ID。',
    'Select stations': '选择测站',
    'Show every station individually, or aggregate markers into clusters': '单独显示每个测站，或将标记聚合显示',
    'SOCKS5 proxy': 'SOCKS5 代理', 'System proxy': '系统代理',
    'System mode: HTTP(S) uses the system proxy; SFTP also uses the discovered HTTP proxy as a CONNECT tunnel when available.': '系统代理模式：HTTP(S) 使用系统代理；如可检测到 HTTP 代理，SFTP 也会通过 CONNECT 隧道使用该代理。',
    'Test Connection': '测试连接',
    'Test results for Chile CSN, Mexico INEGI SFTP and Uruguay IGM will appear here.': 'Chile CSN、Mexico INEGI SFTP 和 Uruguay IGM 的连接测试结果将在此显示。',
    'Testing...': '正在测试…',
    'Visible: 0   Selected: 0': '可见：0   已选：0',
    'Visible: 0   Selected: 0   Hidden selected: 0': '可见：0   已选：0   隐藏已选：0',
    'When automatic decompression is enabled, also keep the original archive.': '启用自动解压时，同时保留原始压缩文件。',
    'Selected': '已选', 'Station': '测站', 'Latitude': '纬度', 'Longitude': '经度',
    'Data Network': '数据网络', 'Regional Source': '区域数据源', 'Network': '网络',
    'Providers': '数据源', 'Filename': '文件名', 'Remote files': '远端文件数',
    'Existing': '已存在', 'To download': '待下载', 'Unavailable': '不可用',
    'Estimated size': '预计大小', 'Close': '关闭',
    'Refreshing {label} station catalog…': '正在刷新 {label} 测站目录…',
    '{label} catalog refresh failed: {message}': '{label} 测站目录刷新失败：{message}',
    '{count} visible': '可见 {count} 个', 'Hide list': '隐藏列表',
    'Paste station IDs': '粘贴测站 ID',
    'Station IDs separated by commas, spaces, or new lines:': '测站 ID 可使用逗号、空格或换行分隔：',
    'Import station IDs': '导入测站 ID',
    'Station list (*.txt *.csv);;All files (*)': '测站列表 (*.txt *.csv);;所有文件 (*)',
    'Brazil · RBMC': '巴西 · RBMC', 'Chile · CSN': '智利 · CSN',
    'Visible: {visible}   Download: all available files for date': '可见：{visible}   下载：该日期全部可用文件',
    '{count} visible · availability discovered at Plan time': '可见 {count} 个 · 在生成下载计划时检查实际可用性',
    '{source} uses the official daily directory. Click a station to switch to an explicit station selection.': '{source} 使用官方日目录；点击测站可切换为明确的单站选择。',
    'Visible: {visible}   Selected: {selected}   Hidden: {hidden}': '可见：{visible}   已选：{selected}   隐藏：{hidden}',
    '{visible} visible · {selected} selected': '可见 {visible} 个 · 已选 {selected} 个',
    'Visible: {visible}   Selected: {selected}   Hidden selected: {hidden}': '可见：{visible}   已选：{selected}   隐藏已选：{hidden}',
    'Drag to pan and click stations to select; Native fallback uses right-drag to pan.': '拖动平移地图并点击测站进行选择；原生离线地图使用右键拖动平移。',
    'Drag a rectangle to add all stations inside it to the selection.': '拖动矩形框，将范围内全部测站加入选择。',
    'Choose a radius, then click the map center to add stations inside it.': '选择半径后点击地图中心，将范围内测站加入选择。',
    'Map: Leaflet / {label}': '地图：Leaflet / {label}',
    'Map: Native offline{detail}': '地图：原生离线{detail}',
    'Auto': '自动',
    'Saved to {path}': '已保存到 {path}',
    'Applied, but could not save: {message}': '设置已应用，但无法保存：{message}',
    'Test failed: {message}': '测试失败：{message}',
    'BBox': '边界框',
    'Quick setup only checks a useful group of product types. It does not lock the selections; you can change them afterwards.': '快速配置只会勾选一组常用产品类型，不会锁定选择；之后仍可自行修改。',
    'Optional. Use a standalone ChromeDriver matching the installed Google Chrome. Leave blank to search GNSSGO_CHROMEDRIVER, PATH, tools/, and drivers/.': '可选。使用与已安装 Google Chrome 匹配的独立 ChromeDriver；留空时将自动搜索 GNSSGO_CHROMEDRIVER、PATH、tools/ 和 drivers/。',
    'Chile': '智利', 'Japan': '日本', 'Korea': '韩国', 'Australia': '澳大利亚',
    'New Zealand': '新西兰', 'Canada': '加拿大', 'United States': '美国',
    'United Kingdom': '英国', 'France': '法国', 'Spain': '西班牙',
    'Netherlands': '荷兰', 'Italy': '意大利', 'Portugal': '葡萄牙',
    'Hong Kong': '中国香港', 'Hong Kong, China': '中国香港', 'Mongolia': '蒙古', 'Singapore': '新加坡',
    'South Africa': '南非', 'Brazil': '巴西', 'Argentina': '阿根廷',
    'Observations': '观测数据', 'Paste IDs': '粘贴 ID', 'Import File': '导入文件',
    'Station list': '测站列表', 'Station map': '测站地图', 'Visible stations': '可见测站',
    'Default archive location': '默认归档目录', 'Provider': '数据源', 'Sampling': '采样率',
    'Output': '输出目录', 'Review Plan': '查看下载计划', 'RINEX': 'RINEX',
    'Data availability': '数据可用性',
    'IGS: automatic download from global data-center mirrors.': 'IGS：通过全球数据中心镜像自动下载。',
    'Select a regional country/source to view data-access details.': '选择区域国家/数据源后可查看数据获取说明。',
    'Japan GEONET: station map/catalog is automatic. RINEX download uses the official Terras web workflow through ChromeDriver (up to 10 stations per browser batch); GSI SFTP requires separate registration and transitioned to RINEX 4.01 in March 2026.': '日本 GEONET：测站地图/目录可自动更新；RINEX 下载通过 ChromeDriver 自动操作官方 Terras 网页（每批最多 10 个站）；GSI SFTP 需要另行注册，并于 2026 年 3 月转为 RINEX 4.01。',
    'Korea KASI/KVN: anonymous FTP automatic download covers the KASINet and KVN subsets.': '韩国 KASI/KVN：匿名 FTP 可自动下载 KASINet 与 KVN 子网数据。',
    'Korea National GNSS Data Center: the catalog covers many more stations, but the official web portal requires interactive web operation; automatic direct download is not available for every displayed station.': '韩国国家 GNSS 数据中心：测站目录覆盖更多站点，但官方网页需要交互操作；图上显示的站点并非都能直接自动下载。',
    'Chile CSN: 1 s daily RINEX; GNSS Go uses ChromeDriver because the CSN web server rejects ordinary automated HTTP clients.': '智利 CSN：提供 1 s 日 RINEX；由于 CSN 服务器会拒绝普通自动 HTTP 客户端，GNSS Go 使用 ChromeDriver 下载。',
    'Mexico RGNA: official INEGI SFTP download; some networks need an HTTP/SOCKS proxy to reach TCP port 22.': '墨西哥 RGNA：通过 INEGI 官方 SFTP 下载；部分网络需要 HTTP/SOCKS 代理才能访问 TCP 22 端口。',
    'Uruguay REGNA-ROU: current-year Hatanaka data use the official FTP day directory; historical data use IGM SFTP.': '乌拉圭 REGNA-ROU：当年 Hatanaka 数据使用官方 FTP 日目录；历史数据使用 IGM SFTP。',
    'Authentication/registration is required by the official source.': '该官方数据源需要认证或注册。',
    'This source requires official web-page interaction or browser automation.': '该数据源需要操作官方网站或使用浏览器自动化。',
    'Automatic download is integrated for the available station/data subset.': '已对可用的测站/数据子集接入自动下载。',
    'GSI GEONET (Terras)': '日本 GSI GEONET（Terras）',
    'Chile · CSN': '智利 · CSN', 'Mexico · INEGI RGNA': '墨西哥 · INEGI RGNA',
    'Uruguay · IGM REGNA-ROU': '乌拉圭 · IGM REGNA-ROU',
    'Search station ID, country, network or source…': '搜索测站 ID、国家/地区、网络或数据源…',
    'Browse stations on one map, select targets, configure the data, then review the download plan.': '在同一张地图上浏览测站、选择目标并配置数据，然后查看下载计划。',
    'Australia Sources': '澳大利亚数据源', 'No sources': '无数据源', 'Fit': '适应范围',
    'Clear selection': '清除选择', 'Start': '开始', 'End': '结束',
    'Browse Stations': '浏览测站', 'Search': '搜索', 'Radius': '半径',
    'Data Network Filter': '数据网络筛选', 'Download Products': '下载产品',
    'Product Request': '产品请求', 'Products': '产品', 'Temporal resolution': '时间分辨率',
    'Product Summary': '产品摘要', 'Quick actions': '快捷操作', 'Downloads': '下载管理',
    'Download queue': '下载队列', 'Details': '详情', 'No download selected': '未选择下载任务',
    'Cancel': '取消', 'Retry Failed': '重试失败项', 'Open folder': '打开文件夹',
    'Activity log': '活动日志', 'Download Navigation': '下载广播星历',
    'Regional data sources': '区域数据源', 'HTTP / HTTPS': 'HTTP / HTTPS',
    'Save': '保存', 'Official source ↗': '官方数据源 ↗', 'Move Up': '上移', 'Move Down': '下移',
    'Browse...': '浏览...',
    'China': '中国大陆',
    'Taiwan': '中国台湾',
    'Taiwan, China': '中国台湾',
    'China · CMONOC': '中国大陆 · CMONOC',
    'Taiwan · GDMS': '中国台湾 · GDMS',
    'Taiwan, China · GDMS': '中国台湾 · GDMS',
    'Official source': '原始/官方网址',
    'Data / copyright notice': '数据与版权说明',
    'Data source': '数据源',
    "Regional sources use a compact one-row view. Europe and South America are shown as multi-country networks; hover Coverage to see the full country list.": '区域数据源采用紧凑单行显示；欧洲和南美洲大网络仅显示“欧洲多国 / 南美多国”，将鼠标悬停在覆盖范围上可查看完整国家列表。',
    'Europe multiple countries': '欧洲多国',
    'South America multiple countries': '南美多国',
    'Coverage': '覆盖范围',
    'EPN / EPOS / national GNSS archives': 'EPN / EPOS / 欧洲国家 GNSS 数据档案',
    'SIRGAS national data centres': 'SIRGAS 各国数据中心',
    'Public / federated official archives': '公开 / 联邦式官方数据档案',
    'National official archives / portals': '各国官方数据档案 / 门户',
    "Third-party data remain subject to each provider's copyright, license, citation and access rules.": '第三方数据仍受各提供机构的版权、许可、引用和访问规则约束。',
    'Automatic download': '自动下载',
    'Japan GEONET: Terras browser download; Auto uses GRJE / RINEX 3.02.': '日本 GEONET：通过 Terras 浏览器下载；Auto 默认使用 GRJE / RINEX 3.02。',
    'Korea KASI/KVN: anonymous FTP automatic download.': '韩国 KASI/KVN：匿名 FTP 自动下载。',
    'Korea National GNSS Data Center: station catalog available; download requires the official web portal.': '韩国国家 GNSS 数据中心：可显示测站目录；下载需通过官方网页。',
    'Taiwan GDMS: GNSS stations are mapped; official download requires registration/login.': '中国台湾 GDMS：已显示 GNSS 测站；官方下载需要注册/登录。',
    'Taiwan, China GDMS: GNSS stations are mapped; official download requires registration/login.': '中国台湾 GDMS：已显示 GNSS 测站；官方下载需要注册/登录。',
    'China CMONOC: station catalog and official source link are provided.': '中国 CMONOC：提供测站目录和官方数据源链接。',
    'Chile CSN: 1 s daily RINEX through browser automation.': '智利 CSN：1 秒日 RINEX，通过浏览器自动下载。',
    'Mexico RGNA: official INEGI SFTP; proxy may be needed for TCP 22.': '墨西哥 RGNA：使用 INEGI 官方 SFTP；TCP 22 可能需要代理。',
    'Uruguay REGNA-ROU: current-year FTP; historical SFTP.': '乌拉圭 REGNA-ROU：当年数据使用 FTP，历史数据使用 SFTP。',
    'Registration/login required.': '需要注册/登录。',
    'Official web interaction/browser automation required.': '需要官方网页操作/浏览器自动化。',
    'Station catalog available; no stable direct download endpoint is assumed.': '可显示测站目录；当前不假定存在稳定的直接下载接口。',
    'Access': '获取方式',
    'See official source': '见官方数据源',
    'Not configured': '未配置',
    'Station metadata are integrated, but no stable public machine-download endpoint is currently assumed.': '已接入测站元数据，但目前不假设存在稳定的公开机器下载接口。',
    "GNSS Go is an independent data-access client. It does not claim ownership of third-party data or websites. Copyright, licenses, terms of use, citation requirements and access restrictions remain with the original providers; please follow each official source\'s rules.": 'GNSS Go 是独立的数据访问工具，不主张对第三方数据或网站拥有所有权。数据版权、许可、使用条款、引用要求及访问限制均归原始提供机构，请遵守各官方数据源的规定。',
    'Taiwan GDMS: the map/catalog includes GNSS, GNSS_IES and GNSS_ETEC stations only. The official GNSS download page requires registration/login; a request may cover at most 7 days, and external-network data have additional portal availability limits.': '中国台湾 GDMS：地图/目录仅保留 GNSS、GNSS_IES 和 GNSS_ETEC 测站。官方 GNSS 下载页面需要注册并登录；单次时间范围最多 7 天，外单位 GNSS 网络还受官网额外的数据时效限制。',
    'China CMONOC: GNSS Go currently indexes the official benchmark-station catalog (263 stations in the bundled snapshot). The source page describes the China Mainland Crustal Movement Observation Network; no undocumented automatic RINEX download endpoint is assumed.': '中国大陆 CMONOC：GNSS Go 当前收录官方基准站目录（内置快照 263 站）。原始页面介绍中国大陆构造环境监测网络；当前不假设存在未公开说明的 RINEX 自动下载接口。',
})

_ZH.update({
    "Provider priority controls automatic global fallback. Regional networks use their own source. Select a provider to reveal its official source link.": "数据源优先级用于控制全球数据的自动回退顺序。区域网络使用各自的数据源；选择数据源可查看其官方网址。",
    "Provider names below open the official network/data source page. Status reflects GNSS Go's current integration level.": "下方数据源名称可打开对应的官方网络/数据页面；状态表示 GNSS Go 当前的接入程度。",
    "GNSS Go is an independent data-access client. It does not claim ownership of third-party data or websites. Copyright, licenses, terms of use, citation requirements and access restrictions remain with the original providers; please follow each official source's rules.": "GNSS Go 是独立的数据访问工具，不主张对第三方数据或网站拥有所有权。数据版权、许可、使用条款、引用要求及访问限制均归原始提供机构，请遵守各官方数据源的规定。",
    "Region": "区域", "Status": "状态", "Access": "获取方式", "Official URL": "原始网址", "GNSS Go": "GNSS Go", "Direct download in the app": "软件内可直接下载",
    "Ready": "可直接使用", "Partial": "部分自动化", "Open source": "打开官方源", "Not live verified": "尚未实网验证",
    "FULL/LIVE": "完全/实网", "PARTIAL/LIVE": "部分/实网", "AUTH": "需认证", "WEB": "网页操作", "BROWSE": "浏览器操作", "MANUAL": "手动", "UNVERIFIED": "未验证",
    "Providers": "数据源",
    "AUSCOPE": "AUSCOPE", "CORSNET-NSW": "CORSNET-NSW", "GPSNET": "GPSNET", "RTKNETWEST": "RTKNETWEST", "SUNPOZ": "SUNPOZ", "NTCORS": "NTCORS", "QLD_TMR": "QLD_TMR", "IPS": "IPS", "UPG": "UPG", "RPS": "RPS", "SMARTNET": "SMARTNET",
    "EPN": "EPN",
    "France · RGP": "法国 · RGP", "Germany · GREF": "德国 · GREF", "Spain · redGAE": "西班牙 · redGAE", "Netherlands · AGRS/NETPOS": "荷兰 · AGRS/NETPOS", "Austria · APOS": "奥地利 · APOS", "Portugal · ReNEP": "葡萄牙 · ReNEP", "Belgium · GNSS.be": "比利时 · GNSS.be", "Greece · NOA/EPOS": "希腊 · NOA/EPOS",
    "Italy · EPOS/GLASS (RING + others)": "意大利 · EPOS/GLASS（RING 等）", "Poland · EPOS/GLASS (ASG-EUPOS)": "波兰 · EPOS/GLASS（ASG-EUPOS）", "Romania · EPOS National Node": "罗马尼亚 · EPOS 国家节点", "United Kingdom · EPOS/GLASS (OS Net)": "英国 · EPOS/GLASS（OS Net）", "Sweden · EPOS/GLASS (SWEPOS)": "瑞典 · EPOS/GLASS（SWEPOS）", "Finland · EPOS/GLASS (FinnRef/FINPOS)": "芬兰 · EPOS/GLASS（FinnRef/FINPOS）", "Switzerland · EPOS/GLASS (AGNES)": "瑞士 · EPOS/GLASS（AGNES）",
    "Hungary · EPOS/GLASS": "匈牙利 · EPOS/GLASS", "Czechia · EPOS/GLASS": "捷克 · EPOS/GLASS", "Slovenia · EPOS/GLASS": "斯洛文尼亚 · EPOS/GLASS", "Ireland · EPOS/GLASS": "爱尔兰 · EPOS/GLASS", "Iceland · EPOS/GLASS": "冰岛 · EPOS/GLASS", "Croatia · EPOS/GLASS": "克罗地亚 · EPOS/GLASS", "Norway · EPOS/GLASS": "挪威 · EPOS/GLASS", "Denmark · EPOS/GLASS": "丹麦 · EPOS/GLASS", "Estonia · EPOS/GLASS": "爱沙尼亚 · EPOS/GLASS", "Latvia · EPOS/GLASS": "拉脱维亚 · EPOS/GLASS", "Lithuania · EPOS/GLASS": "立陶宛 · EPOS/GLASS", "Slovakia · EPOS/GLASS": "斯洛伐克 · EPOS/GLASS", "Bulgaria · EPOS/GLASS": "保加利亚 · EPOS/GLASS", "Cyprus · EPOS/GLASS": "塞浦路斯 · EPOS/GLASS", "Serbia · EPOS/GLASS": "塞尔维亚 · EPOS/GLASS", "Türkiye · EPOS/GLASS": "土耳其 · EPOS/GLASS", "Luxembourg · EPOS/GLASS": "卢森堡 · EPOS/GLASS", "Albania · EPOS/GLASS": "阿尔巴尼亚 · EPOS/GLASS", "Bosnia and Herzegovina · EPOS/GLASS": "波斯尼亚和黑塞哥维那 · EPOS/GLASS", "North Macedonia · EPOS/GLASS": "北马其顿 · EPOS/GLASS", "Moldova · EPOS/GLASS": "摩尔多瓦 · EPOS/GLASS", "Ukraine · EPOS/GLASS": "乌克兰 · EPOS/GLASS", "Malta · EPOS/GLASS": "马耳他 · EPOS/GLASS", "Montenegro · EPOS/GLASS": "黑山 · EPOS/GLASS",
    "Argentina · RAMSAC": "阿根廷 · RAMSAC", "Bolivia · IGM / SIRGAS": "玻利维亚 · IGM / SIRGAS", "Colombia · IGAC / SIRGAS": "哥伦比亚 · IGAC / SIRGAS", "Ecuador · IGM / SIRGAS": "厄瓜多尔 · IGM / SIRGAS", "Peru · IGN / SIRGAS": "秘鲁 · IGN / SIRGAS", "Costa Rica · IGN / SIRGAS": "哥斯达黎加 · IGN / SIRGAS", "Panama · IGNTG / SIRGAS": "巴拿马 · IGNTG / SIRGAS",
    "Hong Kong SatRef": "中国香港 SatRef", "Hong Kong, China · SatRef": "中国香港 · SatRef", "Hong Kong, China SatRef": "中国香港 SatRef", "MONPOS": "蒙古 MONPOS", "SiReNT": "新加坡 SiReNT", "GeoNet New Zealand": "新西兰 GeoNet", "TrigNet": "南非 TrigNet",
    "Direct download in GNSS Go": "软件内可直接下载",
    "Open the official source to download": "需前往官方数据源下载",
    "No integrated regional source yet; select this continent to filter IGS stations.": "暂无独立区域数据源；可选择该洲筛选 IGS 测站。",
    "GNSS Go is an independent data-access client. It does not claim ownership of third-party data or websites. Copyright, licenses, terms of use, citation requirements and access restrictions remain with the original providers; please follow each official source's rules.": "GNSS Go 是独立的数据访问工具，不主张对第三方数据或网站拥有所有权。数据版权、许可、使用条款、引用要求及访问限制均归原始提供机构，请遵守各官方数据源的规定。",
})


# English is the canonical source text; translations are applied at render time
# so switching languages remains reversible regardless of startup language.
_ZH.update({
    "GNSS Data Download": "GNSS 数据下载",
    "Browse stations on one map, select targets, set date and sampling, then review the download plan.": "在一张地图上浏览测站，选择目标、设置日期与采样率，然后查看下载计划。",
    "Paste Stations": "粘贴站点",
    "Current Selection": "当前选择",
    "Plan (PLAN)": "计划 (PLAN)",
    "Region / Data Source": "区域 / 数据源",
    "Select global IGS or regional sources by continent/country.": "选择全球 IGS 或各洲/国家区域数据源。",
    "One-stop GNSS data access and management": "一站式 GNSS 数据获取与管理",
    "Global Stations": "全球测站",
    "{count} stations": "{count} 个测站",
    "{count} networks": "{count} 个网络",
    "Download Tasks": "下载任务",
    "{count} products": "{count} 个产品",
    "Open Observation Download": "进入观测数据下载",
    "Chinese": "中文",
    "English": "英语",
    "system": "跟随系统",
    "light": "浅色",
    "dark": "深色",
    "Mode": "模式",
    "Host": "主机",
    "Port": "端口",
    "Username": "用户名",
    "Password": "密码",
    "Use for": "应用于",
    "ChromeDriver": "ChromeDriver",
    "Python": "Python",
    "Configure one connection route for GNSS downloads. HTTP and SOCKS5 can be used for HTTP/HTTPS; SFTP can tunnel through HTTP CONNECT or SOCKS5. In System mode, HTTP/HTTPS uses the OS/environment proxy; when an HTTP system proxy is discoverable, SFTP can also use it as an HTTP CONNECT tunnel.": "为 GNSS 下载配置统一的网络连接方式。HTTP 和 SOCKS5 可用于 HTTP/HTTPS；SFTP 可通过 HTTP CONNECT 或 SOCKS5 建立隧道。系统代理模式下，HTTP/HTTPS 使用操作系统/环境代理；若能检测到 HTTP 系统代理，SFTP 也可通过该代理的 CONNECT 隧道连接。",
    "OpenStreetMap is used by default when reachable; Offline is the automatic fallback": "网络可访问时默认使用 OpenStreetMap；不可访问时自动回退到离线底图",
    "Only temporal resolution is shown here. A single available interval is selected automatically; when multiple temporal resolutions are available, you can choose one.": "这里只显示时间分辨率。若只有一个可用间隔会自动选择；若有多个可用时间分辨率，可手动选择。",
    "ANTEX: current IGS antenna model is available from the public IGS Central Bureau files.\nSINEX: date-indexed combined solutions are searched in product archives; the current IGS station SINEX is also available from the IGS Central Bureau.": "ANTEX：可从 IGS 官方公开文件获取当前天线模型。\nSINEX：按日期从精密产品归档中查找组合解；当前 IGS 测站 SINEX 也可从 IGS 官方公开文件获取。",
    "Planning download…": "正在生成下载计划…",
    "Plan ready": "下载计划已就绪",
    "Coverage": "覆盖范围",
    "Data source": "数据源",
    "Manual": "手动",
    "Automatic download": "自动下载",
    "Official source": "官方数据源",
    "Korea National GNSS Data Center: automatic 30 s daily ZIP download through the public web session.": "韩国国家 GNSS 数据中心：通过公开网页会话自动下载 30 秒日 ZIP 数据。",
    "SFTP": "SFTP",
    "FTP": "FTP",
    "HTTP / HTTPS": "HTTP / HTTPS",
    "Request ORBIT products": "请求轨道产品",
    "Request CLOCK products": "请求钟差产品",
    "Request ERP products": "请求地球自转参数产品",
    "Request BIAS products": "请求偏差产品",
    "Request IONEX products": "请求电离层产品",
    "Request SINEX products": "请求 SINEX 产品",
    "Request ANTEX products": "请求 ANTEX 产品",
})



class LanguageManager(QtCore.QObject):
    changed = QtCore.Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._language = "en"

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        normalized = "zh" if str(language).lower().startswith("zh") else "en"
        if normalized == self._language:
            return
        self._language = normalized
        self.changed.emit(normalized)

    def tr(self, text: str) -> str:
        return _ZH.get(text, text) if self._language == "zh" else text


language_manager = LanguageManager()

# Reverse lookup is used only when a widget is first cached for live language
# switching.  This matters when the app starts in Chinese: constructors may have
# already rendered tr(English) as Chinese before the widget tree is cached.
_EN_BY_ZH = {}
for _en_text, _zh_text in _ZH.items():
    _EN_BY_ZH.setdefault(_zh_text, _en_text)


def _canonical_source(text: str) -> str:
    value = str(text)
    if language_manager.language == "zh":
        return _EN_BY_ZH.get(value, value)
    return value


def set_language(language: str) -> None:
    language_manager.set_language(language)


def tr(text: str) -> str:
    return language_manager.tr(text)


def translate_dynamic_status(value: str) -> str:
    mapping = {
        "pending": "Pending",
        "planning": "Planning",
        "ready": "Ready",
        "downloading": "Downloading",
        "processing": "Processing",
        "paused": "Paused",
        "completed": "Completed",
        "partial": "Partial",
        "failed": "Failed",
        "cancelled": "Cancelled",
    }
    return tr(mapping.get(value, value))

_I18N_ROLE = int(QtCore.Qt.UserRole) + 77


def retranslate_widget_tree(root) -> None:
    """Retranslate static Qt UI text in-place, reversibly.

    English is treated as the canonical source language.  The source text is
    cached the first time a widget/action/item is seen.  If the application
    started in Chinese, exact Chinese translations are mapped back to their
    English source before caching, so switching back to English is complete.
    """
    widgets = [root, *root.findChildren(_QtWidgets.QWidget)]
    for widget in widgets:
        if isinstance(widget, (_QtWidgets.QLabel, _QtWidgets.QPushButton, _QtWidgets.QToolButton,
                               _QtWidgets.QCheckBox, _QtWidgets.QRadioButton)):
            _translate_text_property(widget, "text", "setText")
        elif isinstance(widget, _QtWidgets.QGroupBox):
            _translate_text_property(widget, "title", "setTitle")

        # Tooltips/status tips were a common source of half-translated screens.
        _translate_aux_property(widget, "toolTip", "setToolTip", "_i18n_tooltip")
        _translate_aux_property(widget, "statusTip", "setStatusTip", "_i18n_status_tip")
        _translate_aux_property(widget, "whatsThis", "setWhatsThis", "_i18n_whats_this")

        if isinstance(widget, (_QtWidgets.QMainWindow, _QtWidgets.QDialog)):
            _translate_aux_property(widget, "windowTitle", "setWindowTitle", "_i18n_window_title")

        if isinstance(widget, _QtWidgets.QLineEdit):
            source = widget.property("_i18n_placeholder")
            if source is None:
                source = _canonical_source(widget.placeholderText())
                widget.setProperty("_i18n_placeholder", source)
            if source:
                widget.setPlaceholderText(tr(str(source)))
        if isinstance(widget, _QtWidgets.QPlainTextEdit):
            source = widget.property("_i18n_placeholder")
            if source is None:
                source = _canonical_source(widget.placeholderText())
                widget.setProperty("_i18n_placeholder", source)
            if source:
                widget.setPlaceholderText(tr(str(source)))
        if isinstance(widget, (_QtWidgets.QSpinBox, _QtWidgets.QDoubleSpinBox)):
            source = widget.property("_i18n_special_value")
            if source is None:
                current = widget.specialValueText()
                if current:
                    source = _canonical_source(current)
                    widget.setProperty("_i18n_special_value", source)
            if source:
                widget.setSpecialValueText(tr(str(source)))
        if isinstance(widget, _QtWidgets.QComboBox):
            for index in range(widget.count()):
                source = widget.itemData(index, _I18N_ROLE)
                if source is None:
                    source = _canonical_source(widget.itemText(index))
                    widget.setItemData(index, source, _I18N_ROLE)
                widget.setItemText(index, tr(str(source)))
        if isinstance(widget, _QtWidgets.QTabWidget):
            for index in range(widget.count()):
                key = f"_i18n_tab_{index}"
                source = widget.property(key)
                if source is None:
                    source = _canonical_source(widget.tabText(index))
                    widget.setProperty(key, source)
                widget.setTabText(index, tr(str(source)))
        if isinstance(widget, _QtWidgets.QTableWidget):
            for col in range(widget.columnCount()):
                item = widget.horizontalHeaderItem(col)
                if item is None:
                    continue
                source = item.data(_I18N_ROLE)
                if source is None:
                    source = _canonical_source(item.text())
                    item.setData(_I18N_ROLE, source)
                item.setText(tr(str(source)))
                tip_source = item.data(_I18N_ROLE + 1)
                if tip_source is None and item.toolTip():
                    tip_source = _canonical_source(item.toolTip())
                    item.setData(_I18N_ROLE + 1, tip_source)
                if tip_source:
                    item.setToolTip(tr(str(tip_source)))

    # QAction objects are not QWidget children but still carry visible menu text.
    try:
        actions = root.findChildren(_QtGui.QAction)
    except Exception:
        actions = []
    for action in actions:
        _translate_text_property(action, "text", "setText")
        _translate_aux_property(action, "toolTip", "setToolTip", "_i18n_tooltip")
        _translate_aux_property(action, "statusTip", "setStatusTip", "_i18n_status_tip")


def _translate_aux_property(obj, getter_name: str, setter_name: str, cache_name: str) -> None:
    getter = getattr(obj, getter_name, None)
    setter = getattr(obj, setter_name, None)
    if not callable(getter) or not callable(setter):
        return
    source = obj.property(cache_name)
    if source is None:
        current = getter()
        if not current:
            return
        source = _canonical_source(current)
        obj.setProperty(cache_name, source)
    if source:
        setter(tr(str(source)))


def _translate_text_property(widget, getter_name: str, setter_name: str) -> None:
    if bool(widget.property("_i18n_dynamic")):
        return
    getter = getattr(widget, getter_name)
    setter = getattr(widget, setter_name)
    source = widget.property("_i18n_source_text")
    if source is None:
        source = _canonical_source(getter())
        widget.setProperty("_i18n_source_text", source)
    if source:
        setter(tr(str(source)))


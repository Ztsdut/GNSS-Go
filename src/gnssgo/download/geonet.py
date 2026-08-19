from __future__ import annotations

import random
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from collections.abc import Callable

from gnssgo.download.chromedriver import (
    _create_session,
    _delete_session,
    _execute_cdp,
    _free_local_port,
    _local_json,
    _wait_driver,
    find_chrome_browser,
    find_chromedriver,
)
from gnssgo.exceptions import DownloadError
from gnssgo.network import ProxyConfig


def _wd(port: int, sid: str, path: str, *, method="GET", payload=None, timeout=10.0):
    return _local_json(
        f"http://127.0.0.1:{port}/session/{sid}{path}",
        method=method,
        payload=payload,
        timeout=timeout,
    )


def _exec(port: int, sid: str, script: str, args=None, timeout=10.0):
    response = _wd(
        port, sid, "/execute/sync", method="POST",
        payload={"script": script, "args": list(args or [])}, timeout=timeout,
    )
    return response.get("value") if isinstance(response, dict) else None


def _navigate(port: int, sid: str, url: str):
    _wd(port, sid, "/url", method="POST", payload={"url": url}, timeout=30.0)


def _current_url(port: int, sid: str) -> str:
    response = _wd(port, sid, "/url", timeout=5.0)
    return str(response.get("value") or "")


def _handles(port: int, sid: str) -> list[str]:
    response = _wd(port, sid, "/window/handles", timeout=5.0)
    return [str(x) for x in (response.get("value") or [])]


def _switch(port: int, sid: str, handle: str) -> None:
    _wd(port, sid, "/window", method="POST", payload={"handle": handle}, timeout=5.0)


def _accept_alert(port: int, sid: str) -> bool:
    try:
        _wd(port, sid, "/alert/accept", method="POST", payload={}, timeout=2.0)
        return True
    except Exception:
        return False


_ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"


def _find_element(port: int, sid: str, using: str, value: str) -> str:
    response = _wd(
        port, sid, "/element", method="POST",
        payload={"using": using, "value": value}, timeout=8.0,
    )
    raw = response.get("value") if isinstance(response, dict) else None
    if not isinstance(raw, dict):
        return ""
    return str(raw.get(_ELEMENT_KEY) or raw.get("ELEMENT") or "")


def _click_element(port: int, sid: str, element_id: str) -> None:
    _wd(
        port, sid, f"/element/{element_id}/click", method="POST",
        payload={}, timeout=10.0,
    )


def _click_first(port: int, sid: str, selectors: list[tuple[str, str]]) -> str:
    """Use a native WebDriver click first, matching the proven Selenium script."""
    last: Exception | None = None
    for using, value in selectors:
        try:
            element_id = _find_element(port, sid, using, value)
            if not element_id:
                continue
            _click_element(port, sid, element_id)
            return value
        except Exception as exc:
            last = exc
    if last is not None:
        raise last
    return ""


def _wait_for_page(port: int, sid: str, needle: str, timeout: float) -> str:
    """Find *needle* in either the current Terras tab or any newly opened tab."""
    observed: dict[str, str] = {}

    def locate() -> str:
        handles = _handles(port, sid)
        for handle in handles:
            try:
                _switch(port, sid, handle)
                current = _current_url(port, sid)
                observed[handle] = current
                if needle in current:
                    return handle
            except Exception:
                continue
        return ""

    try:
        handle = _wait(
            locate, timeout, interval=0.25,
            stage=f"waiting for {needle}",
        )
    except DownloadError as exc:
        urls = " | ".join(observed.values()) or "<unknown>"
        raise DownloadError(
            f"GEONET waiting for {needle} timed out; observed URLs: {urls}"
        ) from exc
    _switch(port, sid, handle)
    return handle


def _choose_select_value_script(element_id: str, wanted: str) -> str:
    # WebDriver Execute Script runs the supplied text as a function body.
    # Unlike a browser console, the value of a trailing expression is NOT
    # automatically returned.  Keep an explicit top-level ``return`` here,
    # otherwise the selector can be changed successfully while Python sees
    # ``None`` forever and reports a false timeout.
    return f"""
return (function(){{
 const el=document.getElementById({element_id!r}) || document.querySelector('select[name="' + {element_id!r} + '"]');
 if(!el) return false;
 const raw=String({wanted!r});
 const normalized=raw.replace(/^0+/, '') || '0';
 const option=Array.from(el.options||[]).find(o => {{
   const v=String(o.value||'').trim();
   const t=String(o.textContent||'').trim();
   return v===raw || t===raw || (v.replace(/^0+/, '')||'0')===normalized || (t.replace(/^0+/, '')||'0')===normalized;
 }});
 if(!option) return false;
 el.value=option.value;
 el.dispatchEvent(new Event('input',{{bubbles:true}}));
 el.dispatchEvent(new Event('change',{{bubbles:true}}));
 return true;
}})();
"""


def _changedate_script(prefix: str) -> str:
    return f"""
return (function(){{
 try{{if(typeof changedate==='function') changedate({prefix!r});}}catch(e){{}}
 return true;
}})();
"""


def _set_day_range(
    port: int, sid: str, prefix: str, iso_date: str, *, label: str
) -> None:
    """Set Terras year/month/day exactly in the sequence used by the reference script."""
    year, month, day = iso_date.split("-")
    _wait(
        lambda: bool(_exec(port, sid, _choose_select_value_script(f"{prefix}_year", year))),
        15, interval=0.25, stage=f"{label} year selector",
    )
    time.sleep(0.15)
    _exec(port, sid, _changedate_script(prefix))

    _wait(
        lambda: bool(_exec(port, sid, _choose_select_value_script(f"{prefix}_mon", month))),
        15, interval=0.25, stage=f"{label} month selector",
    )
    time.sleep(0.25)
    _exec(port, sid, _changedate_script(prefix))

    _wait(
        lambda: bool(_exec(port, sid, _choose_select_value_script(f"{prefix}_day", day))),
        15, interval=0.25, stage=f"{label} day selector",
    )
    _exec(port, sid, _changedate_script(prefix))
    time.sleep(0.25)


def _wait(
    predicate,
    timeout: float,
    interval: float = 0.25,
    *,
    stage: str = "browser step",
):
    """Bounded polling with a stage-specific error message.

    Older builds collapsed every post-selection failure into the same
    ``GEONET browser wait timed out`` message.  Keeping the stage in the
    exception makes it clear whether Terras stopped at the parameter page,
    date selectors, day-download page, or the final file generation step.
    """
    end = time.monotonic() + timeout
    last = None
    while time.monotonic() < end:
        try:
            value = predicate()
            if value:
                return value
        except Exception as exc:
            last = exc
        time.sleep(interval)
    if last:
        raise DownloadError(f"GEONET {stage} timed out: {last}")
    raise DownloadError(f"GEONET {stage} timed out.")


_MARKER_SCRIPT = r"""
const name = arguments[0];
function norm(s){
  if(!s && s !== '') return '';
  try{s=s.toString().normalize('NFKC');}catch(e){}
  s=s.replace(/\u3000/g,' ').replace(/\s+/g,' ').trim();
  return s;
}
const want=norm(name);
const labels=Array.from(document.querySelectorAll('.gsi-iconlabel-class'));
for(const lbl of labels){
  const txt=norm(lbl.innerText || lbl.textContent || '');
  if(txt !== want) continue;
  let el=lbl;
  for(let i=0;i<6 && el;i++){
    if(el.classList && el.classList.contains('leaflet-marker-icon')){
      for(const type of ['mouseover','mousedown','mouseup','click']){
        el.dispatchEvent(new MouseEvent(type,{bubbles:true,cancelable:true}));
      }
      return true;
    }
    el=el.parentElement;
  }
  const img=lbl.querySelector('img'); if(img){img.click(); return true;}
}
return false;
"""


def _set_date_script(prefix: str, iso_date: str) -> str:
    year, month, day = iso_date.split("-")
    return f"""
return (function(){{
 const p={prefix!r};
 function findSelect(suffix){{
   return document.getElementById(p+'_'+suffix) ||
          document.querySelector(`select[name="${{p}}_${{suffix}}"]`);
 }}
 function choose(el, wanted){{
   if(!el) return false;
   const w=String(wanted).replace(/^0+/, '') || '0';
   const opt=Array.from(el.options||[]).find(o => {{
      const v=String(o.value||'').trim();
      const t=String(o.textContent||'').trim();
      const nv=(v.replace(/^0+/, '') || '0');
      const nt=(t.replace(/^0+/, '') || '0');
      return v===String(wanted) || t===String(wanted) || nv===w || nt===w;
   }});
   if(!opt) return false;
   el.value=opt.value;
   el.dispatchEvent(new Event('input',{{bubbles:true}}));
   el.dispatchEvent(new Event('change',{{bubbles:true}}));
   return true;
 }}
 const y=findSelect('year');
 if(!choose(y,{year!r})) return false;
 try{{if(typeof changedate==='function') changedate(p);}}catch(e){{}}
 const m=findSelect('mon');
 if(!choose(m,{month!r})) return false;
 try{{if(typeof changedate==='function') changedate(p);}}catch(e){{}}
 const d=findSelect('day');
 if(!choose(d,{day!r})) return false;
 try{{if(typeof changedate==='function') changedate(p);}}catch(e){{}}
 return true;
}})();
"""


def _select_first_script(element_id: str, choices: list[str]) -> str:
    """Select a Terras option by value *or visible label*.

    Terras has kept the user-facing GRJE / 3.02 labels while its HTML details
    have changed over time.  The user's proven Selenium script selected by
    value, but the raw ChromeDriver implementation should also tolerate a
    renamed id/name or an option whose internal value is no longer the label.
    """
    hint = element_id.replace("day_", "").replace("_ver", "")
    return f"""
return (function(){{
 const wanted={choices!r}.map(x => String(x).trim());
 const norm=s => String(s||'').normalize('NFKC').replace(/\\s+/g,'').toUpperCase();
 const selects=Array.from(document.querySelectorAll('select'));
 let el=document.getElementById({element_id!r}) || document.querySelector('select[name="' + {element_id!r} + '"]');
 if(!el){{
   const hint=norm({hint!r});
   el=selects.find(s => norm(s.id).includes(hint) || norm(s.name).includes(hint));
 }}
 if(!el){{
   el=selects.find(s => Array.from(s.options||[]).some(o => wanted.some(w => {{
      const nw=norm(w), nv=norm(o.value), nt=norm(o.textContent);
      return nv===nw || nt===nw || nt.startsWith(nw) || nt.includes(nw);
   }})));
 }}
 if(!el) return '';
 for(const w of wanted){{
   const nw=norm(w);
   for(const opt of Array.from(el.options||[])){{
      const nv=norm(opt.value), nt=norm(opt.textContent);
      if(nv===nw || nt===nw || nt.startsWith(nw) || nt.includes(nw)){{
        el.value=opt.value;
        el.dispatchEvent(new Event('input',{{bubbles:true}}));
        el.dispatchEvent(new Event('change',{{bubbles:true}}));
        return w;
      }}
   }}
 }}
 return '';
}})();
"""


def _selector_diagnostics_script() -> str:
    return r"""
return Array.from(document.querySelectorAll('select')).map(s => ({
  id:s.id||'', name:s.name||'',
  options:Array.from(s.options||[]).map(o => `${o.value}:${(o.textContent||'').trim()}`).slice(0,20)
}));
"""


def _new_files(folder: Path, before: set[str]) -> list[Path]:
    return [p for p in folder.iterdir() if p.is_file() and p.name not in before and not p.name.endswith('.crdownload')]


def _wait_downloads(folder: Path, before: set[str], timeout: float, progress_callback=None, cancellation_check=None) -> list[Path]:
    deadline = time.monotonic() + timeout
    last_total = -1
    last_progress = time.monotonic()
    saw_activity = False
    stable: dict[str, tuple[int, float]] = {}
    while time.monotonic() < deadline:
        if cancellation_check:
            cancellation_check()
        partials = (
            list(folder.glob('*.crdownload'))
            + list(folder.glob('*.part'))
            + list(folder.glob('*.tmp'))
        )
        candidates = _new_files(folder, before)
        total = 0
        for p in partials + candidates:
            try:
                total += p.stat().st_size
            except OSError:
                pass

        if partials or candidates or total > 0:
            saw_activity = True
        if total != last_total:
            last_total = total
            if saw_activity:
                last_progress = time.monotonic()
            if progress_callback and total > 0:
                progress_callback(total)

        if candidates and not partials:
            now = time.monotonic()
            all_stable = True
            for p in candidates:
                try:
                    size = p.stat().st_size
                except OSError:
                    all_stable = False
                    continue
                if size <= 0:
                    all_stable = False
                    continue
                old = stable.get(p.name)
                if old is None or old[0] != size:
                    stable[p.name] = (size, now)
                    all_stable = False
                elif now - old[1] < 1.5:
                    all_stable = False
            if all_stable:
                return sorted(candidates)

        # Terras may spend several minutes preparing a bundle before Chrome
        # receives the first byte.  Do not treat that server-side preparation
        # as a 120-second browser stall.  Once transfer activity has started,
        # however, a true two-minute no-progress stall is still actionable.
        if saw_activity and time.monotonic() - last_progress > 120:
            raise DownloadError(
                "GEONET browser download stalled for 120 seconds after transfer started."
            )
        time.sleep(0.4)
    raise DownloadError("GEONET browser download exceeded the 20-minute limit.")


def download_geonet_bundle(
    url: str,
    target: Path,
    *,
    station_names: list[str],
    start_date: str,
    end_date: str,
    satellite_choices: list[str],
    rinex_choices: list[str],
    proxy: ProxyConfig,
    chromedriver_path: str | Path | None = None,
    progress_callback: Callable[[int], None] | None = None,
    cancellation_check: Callable[[], None] | None = None,
    status_callback: Callable[[str], None] | None = None,
) -> Path:
    """Download one GEONET/Terras batch using the proven Selenium page flow.

    The flow preserves the validated station-marker selection sequence, then
    continues through the Terras download-parameter page, date/RINEX selection,
    daily download page, bulk confirmation, and final file completion.
    """
    def status(message: str) -> None:
        if status_callback is not None:
            try:
                status_callback(message)
            except Exception:
                pass

    driver = find_chromedriver(chromedriver_path)
    if driver is None:
        raise DownloadError(
            "Japan GEONET browser download requires chromedriver.exe. "
            "Configure it in Settings → Network."
        )
    chrome = find_chrome_browser()
    if chrome is None:
        raise DownloadError("Google Chrome was not found for Japan GEONET browser download.")

    target.parent.mkdir(parents=True, exist_ok=True)
    target.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix="gnssgo_geonet_", dir=str(target.parent)) as temp:
        download_dir = Path(temp)
        port = _free_local_port()
        process = subprocess.Popen(
            [str(driver), f"--port={port}", "--allowed-ips=127.0.0.1", "--log-level=WARNING"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=(
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                if __import__('os').name == 'nt' else 0
            ),
        )
        sid = ""
        try:
            _wait_driver(port)
            sid = _create_session(
                port,
                chrome=chrome,
                download_dir=download_dir,
                proxy=proxy,
                allow_popups=True,
            )
            _execute_cdp(
                port, sid, "Browser.setDownloadBehavior",
                {
                    "behavior": "allow",
                    "downloadPath": str(download_dir.resolve()),
                    "eventsEnabled": True,
                },
            )

            status("Opening Terras map")
            _navigate(port, sid, url)
            _wait(
                lambda: bool(_exec(
                    port, sid,
                    "return document.querySelectorAll('.gsi-iconlabel-class').length > 0;",
                )),
                45,
                stage="waiting for Terras station markers",
            )

            # Keep the exact, already-working marker-name selection logic.  The
            # waits mirror the reference Selenium script so Terras has time to
            # update its internal selected-station list before the submit click.
            selected = 0
            failed: list[str] = []
            for name in station_names:
                if cancellation_check:
                    cancellation_check()
                time.sleep(1.0)
                ok = bool(_exec(port, sid, _MARKER_SCRIPT, [name]))
                time.sleep(0.5)
                if ok:
                    selected += 1
                else:
                    failed.append(name)
                time.sleep(random.uniform(1.0, 3.0))
            if selected == 0:
                raise DownloadError(
                    "GEONET Terras map did not find any of the selected station names."
                )
            status(f"Stations selected ({selected})")
            # Small settle time is important: the visual marker can change before
            # Terras has finished updating the selected-station form.
            time.sleep(1.0)

            # Reference script: click the map page download/submit image.  Use a
            # native WebDriver click first (Selenium's normal behavior), then a JS
            # fallback only if the native element path cannot be used.
            submit_xpath = (
                "/html/body/div[2]/div[3]/div/div/table/tbody/tr/td/div[4]/div[1]/"
                "form[1]/table/tbody/tr/td/div/table/tbody/tr/td/center/img[4]"
            )
            status("Opening Terras download parameters")
            try:
                clicked_selector = _click_first(port, sid, [("xpath", submit_xpath)])
            except Exception:
                clicked_selector = ""
            if not clicked_selector:
                clicked = bool(_exec(port, sid, f"""
const xp={submit_xpath!r};
const el=document.evaluate(xp,document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue;
if(!el) return false; el.click(); return true;
"""))
                if not clicked:
                    raise DownloadError("GEONET Terras submit/download button was not found.")

            # Terras can reuse the current tab or open a new one.  Wait for the
            # actual target URL rather than treating "new tab appeared" as the
            # success condition.
            _wait_for_page(port, sid, "data_download.php", 90)
            status("data_download.php ready")
            time.sleep(1.0)

            # Date controls follow Terras' year -> changedate -> month ->
            # changedate -> day sequence. Selector helper scripts explicitly
            # return their result so accepted selections are not mistaken for a
            # timeout by the raw WebDriver transport.
            try:
                _set_day_range(port, sid, "day_st", start_date, label="start date")
                _set_day_range(port, sid, "day_ed", end_date, label="end date")
            except DownloadError as exc:
                diagnostics = _exec(port, sid, _selector_diagnostics_script())
                raise DownloadError(
                    f"{exc} Terras select controls: {diagnostics!r}"
                ) from exc
            status(f"Date range set: {start_date} to {end_date}")

            try:
                sat = _wait(
                    lambda: _exec(
                        port, sid,
                        _select_first_script('day_satellite', satellite_choices),
                    ),
                    20,
                    interval=0.4,
                    stage="configuring GRJE satellite selector",
                )
            except DownloadError as exc:
                diagnostics = _exec(port, sid, _selector_diagnostics_script())
                raise DownloadError(
                    f"GEONET Terras satellite selector could not be configured; "
                    f"requested={satellite_choices!r}; selects={diagnostics!r}"
                ) from exc
            status(f"Satellite set: {sat}")

            try:
                rinex = _wait(
                    lambda: _exec(
                        port, sid,
                        _select_first_script('day_rinex_ver', rinex_choices),
                    ),
                    20,
                    interval=0.4,
                    stage="configuring RINEX version selector",
                )
            except DownloadError as exc:
                diagnostics = _exec(port, sid, _selector_diagnostics_script())
                raise DownloadError(
                    f"GEONET Terras RINEX-version selector could not be configured; "
                    f"requested={rinex_choices!r}; selects={diagnostics!r}"
                ) from exc
            status(f"RINEX set: {rinex}")
            time.sleep(2.0)

            # Reference script: click "1日毎のデータダウンロード" and do not
            # continue unless day_download.php is actually reached.
            daily_selectors = [
                ("xpath", "//input[@onclick='day_datadownload();']"),
                ("xpath", "//input[contains(@onclick,'day_datadownload')]"),
                ("xpath", "//input[contains(@value,'1日毎のデータダウンロード')]"),
            ]
            status("Submitting daily-data request")
            try:
                daily_clicked = bool(_click_first(port, sid, daily_selectors))
            except Exception:
                daily_clicked = False
            if not daily_clicked:
                daily_clicked = bool(_exec(port, sid, r"""
const el=document.querySelector("input[onclick*='day_datadownload']") ||
  Array.from(document.querySelectorAll('input')).find(x => (x.value||'').includes('1日毎のデータダウンロード'));
if(!el) return false; el.click(); return true;
"""))
            if not daily_clicked:
                raise DownloadError("GEONET Terras daily-data download button was not found.")

            _wait_for_page(port, sid, "day_download.php", 180)
            status("day_download.php ready")
            time.sleep(0.6)

            before = {p.name for p in download_dir.iterdir() if p.is_file()}
            bulk_selectors = [
                ("xpath", "/html/body/div[2]/div[2]/input"),
                ("xpath", "//input[@name='all_download']"),
                ("xpath", "//input[contains(@onclick,'hi_download')]"),
                ("xpath", "//input[contains(@value,'一括') and contains(@value,'ダウンロード')]"),
            ]
            status("Triggering bulk download")
            bulk_clicked = False
            click_error: Exception | None = None
            try:
                bulk_clicked = bool(_click_first(port, sid, bulk_selectors))
            except Exception as exc:
                click_error = exc
                # A synchronous JS confirm can make the click command report
                # "unexpected alert open" even though the click succeeded.
                if _accept_alert(port, sid):
                    bulk_clicked = True
                    status("Bulk-download confirmation accepted")

            if not bulk_clicked:
                try:
                    bulk_clicked = bool(_exec(port, sid, r"""
return (function(){
 const xp='/html/body/div[2]/div[2]/input';
 let el=document.evaluate(xp,document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue ||
        document.querySelector("input[name='all_download']") ||
        document.querySelector("input[onclick*='hi_download']");
 if(el){el.click(); return true;}
 if(typeof hi_download==='function'){hi_download(); return true;}
 return false;
})();
"""))
                except Exception as exc:
                    click_error = exc
                    if _accept_alert(port, sid):
                        bulk_clicked = True
                        status("Bulk-download confirmation accepted")

            if not bulk_clicked:
                raise DownloadError(
                    "GEONET Terras bulk-download button was not found or could not be clicked"
                    + (f": {click_error}" if click_error else ".")
                )

            # Match the proven script: native alert first, then common in-page
            # Japanese/English confirmation controls and callback fallbacks.
            time.sleep(0.6)
            if _accept_alert(port, sid):
                status("Bulk-download confirmation accepted")
            else:
                try:
                    confirmed = bool(_exec(port, sid, r"""
return (function(){
 const words=['はい','確認','OK','ダウンロード','同意','確定'];
 const roots=Array.from(document.querySelectorAll("[role='dialog'], .modal, .dialog"));
 const candidates=[];
 for(const root of roots){
   candidates.push(...root.querySelectorAll('button,a,input[type=button],input[type=submit]'));
 }
 candidates.push(...document.querySelectorAll('button,a,input[type=button],input[type=submit]'));
 for(const el of candidates){
   const txt=((el.innerText||el.textContent||el.value||'')+'').trim();
   if(words.some(w => txt.includes(w))){
      try{el.scrollIntoView({block:'center'});}catch(e){}
      try{el.click(); return true;}catch(e){}
   }
 }
 if(typeof confirm_download==='function'){try{confirm_download(); return true;}catch(e){}}
 if(typeof do_confirm==='function'){try{do_confirm(); return true;}catch(e){}}
 return false;
})();
"""))
                    if confirmed:
                        status("In-page bulk-download confirmation accepted")
                except Exception:
                    pass

            status("Waiting for Terras bundle file")
            files = _wait_downloads(
                download_dir,
                before,
                20 * 60,
                progress_callback,
                cancellation_check,
            )
            if not files:
                raise DownloadError(
                    "GEONET Terras completed without producing a downloadable file."
                )

            if len(files) == 1 and files[0].suffix.lower() == '.zip':
                shutil.copy2(files[0], target)
            else:
                with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                    for path in files:
                        archive.write(path, arcname=path.name)
            status(f"GEONET download complete: {len(files)} file(s)")
            return target
        finally:
            if sid:
                _delete_session(port, sid)
            process.terminate()
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()


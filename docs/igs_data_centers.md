# IGS data-center notes

GNSS Go uses multiple public IGS/GNSS mirrors so observation, navigation and product requests can fall back when one source is unavailable.

## Default observation/navigation priority

```yaml
provider:
  priority: [whu, kasi, esa, ign, sopac, bdsmart, bkgftp, bkg, noaa]
```

## Integrated public providers

- `whu`: Wuhan University IGS Data Center (FTP).
- `kasi`: KASI GNSS Data Center (FTP).
- `esa`: ESA GNSS Science Support Centre (FTP/Web).
- `ign`: IGN IGS Data Center (FTP).
- `sopac`: SOPAC/GARNER archive (HTTPS).
- `bdsmart`: BDSmart IGS archive (HTTPS).
- `bkgftp`: BKG IGS FTP archive.
- `bkg`: BKG IGS HTTPS mirror.
- `noaa`: NOAA NGS CORS observation archive.
- `igsfiles`: IGS Central Bureau current ANTEX and station SINEX files.

Provider priority is configurable in GNSS Go settings/configuration. Regional networks use their own provider routing rather than blindly trying all global mirrors.

## Example

```powershell
gnssgo nav --start 2026-08-01 --end 2026-08-01 --type mixed --provider whu --output data_whu --keep-compressed
gnssgo obs --station WUH200CHN --start 2026-08-01 --end 2026-08-01 --provider whu --output data_whu --keep-compressed
```

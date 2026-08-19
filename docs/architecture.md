# GNSS Go Architecture

GNSS Go keeps provider discovery, downloading, post-processing, archive layout, and CLI
presentation separated.

```text
User
  -> CLI / Python API
  -> Request Model
  -> Query / Resolver
  -> Provider Layer
  -> Download Manager
  -> Post Processor
  -> Local Archive
```

The CLI constructs typed requests and delegates to `GNSSGo`. Providers produce `RemoteFile`
objects only. `DownloadManager` receives `DownloadTask` objects and does not know which provider
created them.

from gnssgo.providers.mirrors import IGSMirrorPathResolver, IGSMirrorProvider, _yy_subdir_resolver

WHU_ARCHIVE = "ftp://igs.gnsswhu.cn/pub/gps"


class WHUPathResolver(IGSMirrorPathResolver):
    def __init__(self) -> None:
        base = WHU_ARCHIVE
        template = _yy_subdir_resolver(base)
        super().__init__(
            base_url=template.base_url,
            observation_directory=template._observation_directory,
            navigation_directories=template._navigation_directories,
            product_directory=template._product_directory,
        )


class WHUProvider(IGSMirrorProvider):
    def __init__(
        self,
        resolver: WHUPathResolver | None = None,
        check_existing: bool = True,
    ) -> None:
        super().__init__(
            "whu",
            resolver or WHUPathResolver(),
            check_existing=check_existing,
        )

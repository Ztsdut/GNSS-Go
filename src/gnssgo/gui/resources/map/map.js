const markerById = new Map();
const stationById = new Map();

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

window.GNSSGoMap = {
  bridge: null,
  landLayer: null,
  landGeoJson: null,
  tileLayer: null,
  stations: [],
  selected: new Set(),
  map: null,
  cluster: null,
  individualLayer: null,
  markerMode: "individual",
  tool: "select",
  dragStart: null,
  rectangle: null,
  radiusCircle: null,
  radiusKm: 500,
  theme: "light",
  language: "en",
  basemap: "offline",

  init() {
    const notice = document.getElementById("notice");
    if (!window.L) {
      notice.textContent = "Leaflet assets are unavailable; GNSS Go will use the native fallback map.";
      return;
    }
    try {
      this.map = L.map("map", {
        worldCopyJump: true,
        zoomControl: true,
        attributionControl: true,
        preferCanvas: true,
        minZoom: 1,
        maxZoom: 18
      }).setView([20, 0], 2);
      this.individualLayer = L.featureGroup().addTo(this.map);
      this.cluster = this.createClusterLayer();
      this.bindMapTools();
      this.setTheme("light");
      this.setLanguage("en");
      this.setBasemap("offline");
      setTimeout(() => this.map.invalidateSize(false), 0);
    } catch (error) {
      notice.textContent = `Leaflet initialization failed: ${error}`;
      console.error(error);
    }
  },

  createClusterLayer() {
    return L.markerClusterGroup({
      chunkedLoading: true,
      maxClusterRadius: 42,
      showCoverageOnHover: false,
      removeOutsideVisibleBounds: true,
      iconCreateFunction: (cluster) => this.clusterIcon(cluster)
    });
  },

  resetStationLayers() {
    if (!this.map) return;
    // Recreate both station layers instead of reusing them. MarkerCluster's
    // chunked addLayers() can still have queued work when the user switches
    // networks; reusing the same cluster object allows an old chunk to appear
    // after clearLayers(). A detached old layer may finish in the background,
    // but it can no longer put stale markers back on the visible map.
    if (this.cluster) {
      if (this.map.hasLayer(this.cluster)) this.map.removeLayer(this.cluster);
      this.cluster.clearLayers();
    }
    if (this.individualLayer) {
      if (this.map.hasLayer(this.individualLayer)) this.map.removeLayer(this.individualLayer);
      this.individualLayer.clearLayers();
    }
    this.cluster = this.createClusterLayer();
    this.individualLayer = L.featureGroup();
    markerById.clear();
    stationById.clear();
  },

  bindMapTools() {
    if (!this.map) return;
    this.map.on("click", (event) => {
      if (this.tool !== "radius") return;
      if (this.radiusCircle) this.radiusCircle.remove();
      this.radiusCircle = L.circle(event.latlng, {
        radius: this.radiusKm * 1000,
        color: this.theme === "dark" ? "#78bddb" : "#1b6f93",
        fillOpacity: 0.08
      }).addTo(this.map);
      this.selectStationsInRadius(event.latlng, this.radiusKm);
      if (this.bridge) this.bridge.send_radius(event.latlng.lat, event.latlng.lng, this.radiusKm);
    });

    this.map.on("mousedown", (event) => {
      if (this.tool !== "rectangle") return;
      this.dragStart = event.latlng;
      this.map.dragging.disable();
    });

    this.map.on("mousemove", (event) => {
      if (this.tool !== "rectangle" || !this.dragStart) return;
      const bounds = L.latLngBounds(this.dragStart, event.latlng);
      if (!this.rectangle) {
        this.rectangle = L.rectangle(bounds, {
          color: this.theme === "dark" ? "#78bddb" : "#1b6f93",
          weight: 1,
          dashArray: "5 4",
          fillOpacity: 0.06
        }).addTo(this.map);
      }
      this.rectangle.setBounds(bounds);
    });

    this.map.on("mouseup", (event) => {
      if (this.tool !== "rectangle" || !this.dragStart) return;
      const bounds = L.latLngBounds(this.dragStart, event.latlng);
      this.dragStart = null;
      this.map.dragging.enable();
      this.selectStationsInBounds(bounds);
      if (this.bridge) {
        this.bridge.send_bbox(
          bounds.getWest(),
          bounds.getSouth(),
          bounds.getEast(),
          bounds.getNorth()
        );
      }
    });
  },

  setTool(tool) {
    this.tool = ["select", "rectangle", "radius"].includes(tool) ? tool : "select";
    if (!this.map) return;
    if (this.tool === "rectangle") this.map.dragging.disable();
    else this.map.dragging.enable();
    this.map.getContainer().style.cursor = ["rectangle", "radius"].includes(this.tool)
      ? "crosshair"
      : "";
  },

  setRadiusKm(radiusKm) {
    const value = Number(radiusKm);
    if (Number.isFinite(value) && value > 0) this.radiusKm = value;
  },

  setMarkerMode(mode) {
    this.markerMode = mode === "cluster" ? "cluster" : "individual";
    this.renderMarkers();
  },

  setStations(stations) {
    this.stations = (stations || []).filter((station) => {
      // Number(null) === 0 and Number("") === 0 in JavaScript.  Treat missing
      // coordinates as missing instead of silently plotting catalogue-only
      // stations at (0, 0).
      if (station.lat === null || station.lat === undefined || station.lat === "" ||
          station.lon === null || station.lon === undefined || station.lon === "") {
        return false;
      }
      const lat = Number(station.lat);
      const lon = Number(station.lon);
      return Number.isFinite(lat) && Number.isFinite(lon) &&
        lat >= -90 && lat <= 90 && lon >= -180 && lon <= 180;
    });
    this.renderMarkers();
  },

  setLand(geojson) {
    if (!this.map || !geojson) return;
    this.landGeoJson = geojson;
    if (this.landLayer && this.map.hasLayer(this.landLayer)) {
      this.map.removeLayer(this.landLayer);
    }
    this.landLayer = L.geoJSON(geojson, {
      interactive: false,
      style: () => this.landStyle()
    });
    if (this.basemap === "offline") {
      this.landLayer.addTo(this.map);
      this.landLayer.bringToBack();
    }
  },

  landStyle() {
    if (this.theme === "dark") {
      return { color: "#5c7181", weight: 1, fillColor: "#2b3b45", fillOpacity: 1 };
    }
    return { color: "#96aab8", weight: 1, fillColor: "#d7e2d7", fillOpacity: 1 };
  },

  setTheme(theme) {
    this.theme = theme === "dark" ? "dark" : "light";
    document.body.classList.toggle("theme-dark", this.theme === "dark");
    document.body.classList.toggle("theme-light", this.theme !== "dark");
    if (this.landLayer) this.landLayer.setStyle(this.landStyle());
    markerById.forEach((marker, id) => {
      this.applyMarkerStyle(marker, stationById.get(id), this.selected.has(id));
    });
  },

  setLanguage(language) {
    this.language = String(language || "en").toLowerCase().startsWith("zh") ? "zh" : "en";
    this.updateLegend();
  },

  updateLegend() {
    const labels = this.language === "zh"
      ? { igs: "IGS 全球站", regional: "区域 CORS", overlap: "IGS + 区域", selected: "已选择" }
      : { igs: "IGS global", regional: "Regional CORS", overlap: "IGS + Regional", selected: "Selected" };
    const values = [
      ["legend-igs", labels.igs],
      ["legend-regional", labels.regional],
      ["legend-overlap", labels.overlap],
      ["legend-selected", labels.selected]
    ];
    values.forEach(([id, value]) => {
      const element = document.getElementById(id);
      if (element) element.textContent = value;
    });
  },

  setBasemap(name) {
    this.basemap = name === "osm" ? "osm" : "offline";
    if (!this.map) return;
    const notice = document.getElementById("notice");

    // Basemaps are mutually exclusive.  The offline GeoJSON lives in Leaflet's
    // overlay pane, so leaving it mounted above OSM makes the UI look like two
    // maps are stacked.  Explicitly remove one before enabling the other.
    if (this.tileLayer && this.map.hasLayer(this.tileLayer)) {
      this.map.removeLayer(this.tileLayer);
    }
    this.tileLayer = null;
    if (this.landLayer && this.map.hasLayer(this.landLayer)) {
      this.map.removeLayer(this.landLayer);
    }

    if (this.basemap === "osm") {
      this.tileLayer = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
        maxZoom: 18,
        attribution: "&copy; OpenStreetMap contributors"
      });
      let loadedTiles = 0;
      let failedTiles = 0;
      this.tileLayer.on("tileload", () => {
        loadedTiles += 1;
        if (loadedTiles === 1) notice.textContent = "OpenStreetMap basemap";
      });
      this.tileLayer.on("tileerror", () => {
        failedTiles += 1;
        if (loadedTiles === 0 && failedTiles >= 4) {
          notice.textContent = "OpenStreetMap tiles could not be loaded. The station map is still available; choose Offline basemap if needed.";
        }
      });
      this.tileLayer.addTo(this.map);
      notice.textContent = "Loading OpenStreetMap...";
    } else {
      if (this.landLayer) {
        this.landLayer.addTo(this.map);
        this.landLayer.bringToBack();
      }
      notice.textContent = "Offline vector basemap";
    }
  },

  renderMarkers() {
    if (!this.map) return;
    const currentCenter = this.map.getCenter();
    const currentZoom = this.map.getZoom();
    this.resetStationLayers();

    if (this.markerMode === "cluster") {
      const markers = this.stations.map((station) => {
        stationById.set(station.id, station);
        const selected = this.selected.has(station.id);
        const marker = L.marker([Number(station.lat), Number(station.lon)], {
          icon: this.markerIcon(station, selected),
          keyboard: false,
          gnssClass: this.stationClass(station),
          gnssSelected: selected
        });
        this.bindStationInteractions(marker, station);
        markerById.set(station.id, marker);
        return marker;
      });
      this.cluster.addLayers(markers);
      this.map.addLayer(this.cluster);
    } else {
      const markers = this.stations.map((station) => {
        stationById.set(station.id, station);
        const marker = L.circleMarker(
          [Number(station.lat), Number(station.lon)],
          this.circleStyle(station, this.selected.has(station.id))
        );
        this.bindStationInteractions(marker, station);
        markerById.set(station.id, marker);
        return marker;
      });
      markers.forEach((marker) => this.individualLayer.addLayer(marker));
      this.map.addLayer(this.individualLayer);
    }

    // Filtering or first station load must never move the map.  GNSS Go opens
    // at a global view and only the explicit Fit action changes the viewport.
    this.map.setView(currentCenter, currentZoom, { animate: false });
  },

  bindStationInteractions(marker, station) {
    marker.bindTooltip(escapeHtml(station.id), { direction: "top", opacity: 0.95 });
    marker.bindPopup(this.stationPopup(station));
    marker.on("click", () => this.toggleStation(station.id));
  },

  stationPopup(station) {
    const dataNetworks = (station.data_networks || []).map(escapeHtml).join(", ") || "-";
    const regionalSources = (station.regional_sources || []).map(escapeHtml).join(", ") || "-";
    const providers = (station.providers || []).map(escapeHtml).join(", ") || "-";
    const country = escapeHtml(station.country || "-");
    const labels = this.language === "zh"
      ? { country: "国家/地区", network: "数据网络", regional: "区域来源", providers: "可用数据源" }
      : { country: "Country", network: "Data Network", regional: "Regional Source", providers: "Available Providers" };
    return `
      <div class="station-popup">
        <strong>${escapeHtml(station.id)}</strong><br>
        ${Number(station.lat).toFixed(4)}, ${Number(station.lon).toFixed(4)}<br>
        <small>${labels.country}</small><br>${country}<br>
        <small>${labels.network}</small><br>${dataNetworks}<br>
        <small>${labels.regional}</small><br>${regionalSources}<br>
        <small>${labels.providers}</small><br>${providers}
      </div>
    `;
  },

  stationClass(station) {
    const value = String((station && station.marker_class) || "other");
    return value === "igs_regional" ? "igs_only" : (["igs_only", "regional_only"].includes(value) ? value : "other");
  },

  markerPalette(markerClass) {
    const dark = this.theme === "dark";
    const palettes = dark
      ? {
          igs_only: { fill: "#4FA3FF", outline: "#F4F7FA" },
          regional_only: { fill: "#FF9F43", outline: "#F4F7FA" },
          igs_regional: { fill: "#4FA3FF", outline: "#F4F7FA" },
          other: { fill: "#E67E22", outline: "#F4F7FA" }
        }
      : {
          igs_only: { fill: "#2563EB", outline: "#FFFFFF" },
          regional_only: { fill: "#E67E22", outline: "#FFFFFF" },
          igs_regional: { fill: "#2563EB", outline: "#FFFFFF" },
          other: { fill: "#E67E22", outline: "#FFFFFF" }
        };
    return palettes[markerClass] || palettes.other;
  },

  markerIcon(station, selected) {
    const markerClass = this.stationClass(station);
    return L.divIcon({
      className: "",
      html: `<div class="station-marker ${markerClass}${selected ? " selected" : ""}"></div>`,
      iconSize: selected ? [16, 16] : [12, 12],
      iconAnchor: selected ? [8, 8] : [6, 6]
    });
  },

  circleStyle(station, selected) {
    const markerClass = this.stationClass(station);
    const palette = this.markerPalette(markerClass);
    return {
      radius: selected ? 6 : 4,
      color: selected ? (this.theme === "dark" ? "#FF5C5C" : "#DC2626") : palette.outline,
      weight: selected ? 3 : 1.5,
      fillColor: palette.fill,
      fillOpacity: 0.96
    };
  },

  clusterIcon(cluster) {
    const children = cluster.getAllChildMarkers();
    const classes = new Set(children.map((marker) => marker.options.gnssClass || "other"));
    const markerClass = classes.has("igs_only") || classes.has("igs_regional")
      ? "igs_only"
      : (classes.has("regional_only") ? "regional_only" : "other");
    const selected = children.some((marker) => Boolean(marker.options.gnssSelected));
    const html = `<div class="station-cluster ${markerClass}${selected ? " selected" : ""}"><span>${cluster.getChildCount()}</span></div>`;
    return L.divIcon({ className: "gnss-cluster-icon", html, iconSize: [32, 32], iconAnchor: [16, 16] });
  },

  applyMarkerStyle(marker, station, selected) {
    if (!marker) return;
    if (typeof marker.setStyle === "function") {
      marker.setStyle(this.circleStyle(station, selected));
    } else if (typeof marker.setIcon === "function") {
      marker.options.gnssSelected = selected;
      marker.options.gnssClass = this.stationClass(station);
      marker.setIcon(this.markerIcon(station, selected));
      if (this.cluster && typeof this.cluster.refreshClusters === "function") {
        this.cluster.refreshClusters(marker);
      }
    }
  },

  toggleStation(id) {
    const selected = !this.selected.has(id);
    if (selected) this.selected.add(id);
    else this.selected.delete(id);
    const marker = markerById.get(id);
    if (marker) this.applyMarkerStyle(marker, stationById.get(id), selected);
    if (this.bridge) this.bridge.toggle_station(id, selected);
  },

  selectStationsInBounds(bounds) {
    this.stations.forEach((station) => {
      if (!bounds.contains([Number(station.lat), Number(station.lon)]) || this.selected.has(station.id)) return;
      this.selected.add(station.id);
      const marker = markerById.get(station.id);
      if (marker) this.applyMarkerStyle(marker, station, true);
      if (this.bridge) this.bridge.toggle_station(station.id, true);
    });
  },

  selectStationsInRadius(center, radiusKm) {
    this.stations.forEach((station) => {
      const distanceKm = center.distanceTo(L.latLng(Number(station.lat), Number(station.lon))) / 1000.0;
      if (distanceKm > radiusKm || this.selected.has(station.id)) return;
      this.selected.add(station.id);
      const marker = markerById.get(station.id);
      if (marker) this.applyMarkerStyle(marker, station, true);
      if (this.bridge) this.bridge.toggle_station(station.id, true);
    });
  },

  clearSelection(notifyBridge = true) {
    [...this.selected].forEach((id) => {
      this.selected.delete(id);
      const marker = markerById.get(id);
      if (marker) this.applyMarkerStyle(marker, stationById.get(id), false);
    });
    if (this.rectangle) {
      this.rectangle.remove();
      this.rectangle = null;
    }
    if (this.radiusCircle) {
      this.radiusCircle.remove();
      this.radiusCircle = null;
    }
    if (notifyBridge && this.bridge) this.bridge.clear_selection();
  },

  fitSelection() {
    const selectedMarkers = [...this.selected].map((id) => markerById.get(id)).filter(Boolean);
    if (selectedMarkers.length) {
      this.map.fitBounds(L.featureGroup(selectedMarkers).getBounds(), { padding: [24, 24], maxZoom: 12 });
    } else {
      this.fitVisible();
    }
  },

  fitWorld() {
    if (this.map) this.map.setView([20, 0], 2, { animate: false });
  },

  fitVisible() {
    const activeLayer = this.markerMode === "cluster" ? this.cluster : this.individualLayer;
    if (activeLayer && activeLayer.getLayers().length) {
      this.map.fitBounds(activeLayer.getBounds(), { padding: [24, 24], maxZoom: 12 });
    } else if (this.landLayer) {
      this.map.fitBounds(this.landLayer.getBounds(), { padding: [8, 8] });
    } else {
      this.map.setView([20, 0], 2);
    }
  },

  refreshSize() {
    if (this.map) this.map.invalidateSize(false);
  },

  setSelected(ids) {
    this.selected = new Set(ids || []);
    markerById.forEach((marker, id) => {
      this.applyMarkerStyle(marker, stationById.get(id), this.selected.has(id));
    });
  },

  diagnostics() {
    const mapElement = document.getElementById("map");
    return {
      leaflet: !!window.L,
      map: !!this.map,
      cluster: !!this.cluster,
      individualLayer: !!this.individualLayer,
      markerMode: this.markerMode,
      width: mapElement ? mapElement.clientWidth : 0,
      height: mapElement ? mapElement.clientHeight : 0,
      stations: this.stations.length
    };
  }
};

window.GNSSGoMap.init();

if (window.qt && window.qt.webChannelTransport && window.QWebChannel) {
  new QWebChannel(qt.webChannelTransport, function(channel) {
    window.GNSSGoMap.bridge = channel.objects.bridge;
    if (window.GNSSGoMap.bridge && window.GNSSGoMap.bridge.map_ready) {
      window.GNSSGoMap.bridge.map_ready();
    }
  });
}

from gnssgo import GNSSGo

client = GNSSGo()
plan = client.plan_observations(
    stations=["WUH200CHN"],
    start="2026-08-01",
    end="2026-08-02",
)
print(f"remote files: {len(plan.remote_files)}")

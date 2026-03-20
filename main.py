from sim.training_runner import TrainingRunner

print("TITAN FORGE - Starting simulation training...")
print("Running multiple protocol cycles to mature all capabilities...")

r = TrainingRunner()

for i in range(200):
    report = r.run_full_protocol()
    if report.cleared_for_live:
        print(f"CLEARED after {i+1} cycles!")
        break
    if (i + 1) % 20 == 0:
        print(f"Cycle {i+1}/200: {len(report.blocking_reasons)} blocking issue(s) remain.")

print(report.summary)

from sim.training_runner import TrainingRunner 
r = TrainingRunner() 
[r.run_full_protocol() for _ in range(200)] 
report = r.run_full_protocol() 
print(report.summary) 

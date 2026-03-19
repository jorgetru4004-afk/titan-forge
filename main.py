import asyncio 
from sim.training_runner import TrainingRunner 
print("TITAN FORGE - Starting simulation training...") 
runner = TrainingRunner() 
report = runner.run_full_protocol() 
print(report.summary) 

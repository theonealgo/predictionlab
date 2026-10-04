from weighted_total_predictor import calculate_weighted_average_total
import logging

logging.basicConfig(level=logging.INFO)

try:
    print("Calculating weighted total...")
    result = calculate_weighted_average_total("Boston Celtics", "Milwaukee Bucks", 225.5, sport='NBA')
    print(f"Result: {result}")
except Exception as e:
    print(f"Error: {e}")

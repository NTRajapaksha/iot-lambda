# Energy Forecasting with Lambda Architecture

This project implements a **Lambda architecture** to process and analyze energy consumption data from IoT devices. It combines **batch processing** for historical data analysis with **real-time streaming** for immediate insights, enabling accurate energy demand forecasting and anomaly detection. Built with scalable tools like **Apache Spark**, **Kafka**, **HDFS**, and **Flask**, the system provides a user-friendly dashboard and APIs to access results, making it valuable for utility companies and smart home users.

## Introduction

The rapid growth of Internet of Things (IoT) technologies in energy management generates vast amounts of data, creating opportunities for optimization while presenting significant analytical challenges. This project addresses two key objectives:

1. **Real-Time Anomaly Detection**: Identifying irregular energy consumption patterns to prevent inefficiencies or equipment failures.
2. **Accurate Demand Forecasting**: Predicting future energy needs to enable proactive resource planning.

By tackling these challenges, the system enhances energy efficiency and supports better decision-making in IoT-enabled environments.

## Stakeholders

The system serves two primary groups:

- **Utility Companies**: Monitor energy distribution networks, detect anomalies (e.g., grid inefficiencies), and predict demand to optimize generation and allocation.
- **Smart Home Users**: Receive real-time alerts for unusual energy usage (e.g., faulty appliances) and predictive insights to manage consumption and reduce costs.

## Project Architecture

The project follows a **Lambda architecture**, which separates data processing into three layers: **Batch**, **Streaming**, and **Serving**. This ensures both comprehensive historical analysis and low-latency real-time insights.

### 1. Batch Layer
The batch layer processes historical energy data to generate predictive models and statistical insights.

- **Components**:
  - **`ingest.py`**: Reads raw energy data from a CSV file, cleans it (e.g., handles missing values, creates timestamps), and stores it as **Parquet files** in HDFS (`hdfs://namenode:9000/input/energy_data`) or locally. It also computes and saves statistical summaries (e.g., hourly averages) to a JSON file.
  - **`train.py`**: Trains a **Random Forest Regressor** to predict energy consumption (`active_power`). It performs feature engineering (e.g., extracting hour, day of week), evaluates the model (RMSE, R², MAE), and saves the model and predictions to HDFS and SQLite.
  - **`benchmark.py`**: Compares the performance of Spark against a simulated MapReduce framework, measuring processing time, memory usage, and CPU load for different data sizes (50MB, 100MB, 200MB). Results are stored in SQLite and visualized as a chart.

- **Why Parquet?**
  - **Efficient Storage**: Columnar format with compression reduces disk usage.
  - **Fast Queries**: Column pruning and predicate pushdown speed up Spark operations.
  - **Scalability**: Compatible with HDFS for distributed storage, with data split into three Parquet files for parallel processing.

### 2. Streaming Layer
The streaming layer processes real-time IoT energy data to detect anomalies.

- **Components**:
  - **`producer.py`**: Simulates IoT energy data (calibrated with historical statistics) and streams it to a Kafka topic (`energy-stream`) or a socket server. It introduces anomalies (5% chance) to test detection.
  - **`detect.py`**: Uses **Spark Structured Streaming** to consume streaming data from Kafka, processes it in 10-second micro-batches, and detects anomalies using the 3-sigma rule. Results are saved to SQLite or JSON files.

- **Key Features**:
  - **Real-Time Processing**: Spark Structured Streaming ensures low-latency anomaly detection.
  - **Fault Tolerance**: Checkpointing in `detect.py` allows recovery from failures.
  - **Flexibility**: Fallback to simulated data if Kafka is unavailable.

### 3. Serving Layer
The serving layer presents results to users via a web interface and APIs.

- **Components**:
  - **`app.py`**: A Flask application that:
    - Displays a dashboard (`dashboard.html`) showing recent anomalies, forecasts, and model performance metrics (e.g., RMSE, R²).
    - Provides APIs (`/api/anomalies`, `/api/forecasts`, `/api/model_info`, `/api/counts`) for dynamic data access.
    - Enables users to input data for real-time energy predictions using the trained model.
    - Supports model reloading via an admin endpoint (`/reload_model`).

- **Data Storage**:
  - Results (anomalies, forecasts, model info) are stored in a SQLite database (`energy_forecasting.db`) for easy access.
  - Models are loaded from HDFS, ensuring the latest version is used.

### System Workflow
1. **Batch Layer**:
   - `ingest.py` processes raw data and stores it as Parquet files.
   - `train.py` trains a model and saves predictions to SQLite.
   - `benchmark.py` evaluates performance and stores results.
2. **Streaming Layer**:
   - `producer.py` generates real-time data and sends it to Kafka.
   - `detect.py` detects anomalies and stores them in SQLite.
3. **Serving Layer**:
   - `app.py` fetches data from SQLite, displays it on a dashboard, and serves predictions via a form or APIs.

## Technologies Used
- **Apache Spark**: For batch processing (data ingestion, model training) and streaming (anomaly detection).
- **Kafka**: Streams real-time energy data.
- **HDFS**: Stores Parquet files for distributed data management.
- **Flask**: Powers the web dashboard and APIs.
- **SQLite**: Stores results (anomalies, forecasts, metrics).
- **Docker**: Containerizes services for easy deployment.
- **MongoDB & Redis**: Included in the setup for potential caching or additional storage (not used in provided code).

## Setup and Installation

### Prerequisites
- **Docker** and **Docker Compose** installed.
- A machine with at least 8GB RAM (due to resource limits in `docker-compose.yml`).

### Repository Structure
```
├── code/
│   ├── batch/            # Batch layer scripts (ingest.py, train.py, benchmark.py)
│   ├── streaming/        # Streaming layer scripts (producer.py, detect.py)
│   └── serving/          # Serving layer (app.py, HTML/CSS templates)
├── config/               # Configuration files (Hadoop, Spark, Kafka)
├── data/                 # Shared data volume for Parquet, SQLite, etc.
├── hadoop/               # HDFS volumes (namenode, datanode)
├── spark-tmp/            # Temporary Spark files
└── docker-compose.yml    # Docker service definitions
```

### Steps to Run
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/<your-username>/energy-forecasting-lambda.git
   cd energy-forecasting-lambda
   ```

2. **Prepare Data**:
   - Place your raw energy data CSV (e.g., `energy_data.csv`) in the `data/` directory.
   - Ensure the CSV has columns like `Date`, `Time`, `Global_active_power`, etc., as expected by `ingest.py`.

3. **Start Docker Containers**:
   ```bash
   docker-compose up -d
   ```
   This starts services including HDFS (namenode, datanode), Spark (master, worker), Kafka, Zookeeper, Flask, MongoDB, and Redis.

4. **Run Batch Processing**:
   - Access the Spark master container:
     ```bash
     docker exec -it spark-master bash
     ```
   - Run ingestion and training:
     ```bash
     spark-submit /app/code/batch/ingest.py
     spark-submit /app/code/batch/train.py
     ```
   - Run benchmarking:
     ```bash
     spark-submit /app/code/batch/benchmark.py
     ```

5. **Run Streaming Processing**:
   - Access the Kafka container:
     ```bash
     docker exec -it kafka bash
     ```
   - Start the data producer:
     ```bash
     python /app/streaming/producer.py
     ```
   - In another terminal, access the Spark master and run anomaly detection:
     ```bash
     docker exec -it spark-master bash
     spark-submit /app/code/streaming/detect.py
     ```

6. **Access the Dashboard**:
   - Open a browser and navigate to `http://localhost:5000` to view the Flask dashboard.
   - Use the prediction form to input data and get energy forecasts.
   - Check APIs (e.g., `http://localhost:5000/api/anomalies`) for JSON data.

7. **Monitor Services**:
   - HDFS Web UI: `http://localhost:9870`
   - Spark Master UI: `http://localhost:8080`
   - Kafka is accessible at `kafka:9092` within the Docker network.

### Stopping the Services
```bash
docker-compose down
```

## Key Features
- **Real-Time Anomaly Detection**: Identifies unusual energy usage patterns within seconds using Spark Structured Streaming.
- **Accurate Forecasting**: Predicts energy consumption with a Random Forest model trained on historical data.
- **Scalable Storage**: Uses Parquet files in HDFS for efficient, distributed data management.
- **Interactive Dashboard**: Displays anomalies, forecasts, and model metrics, with a form for on-demand predictions.
- **Performance Benchmarking**: Compares Spark and MapReduce, with results visualized as charts.

## Future Improvements
- **Advanced Models**: Experiment with gradient boosting or neural networks for better prediction accuracy.
- **Enhanced Anomaly Detection**: Use machine learning (e.g., isolation forests) instead of the 3-sigma rule.
- **Real-Time Alerts**: Add notifications for anomalies via email or messaging.
- **Scalability**: Optimize Parquet partitioning for larger datasets and adjust Spark configurations.
- **Dashboard Enhancements**: Add interactive charts and filters for better user experience.

## Contributing
Contributions are welcome! Please:
1. Fork the repository.
2. Create a feature branch (`git checkout -b feature/your-feature`).
3. Commit changes (`git commit -m 'Add your feature'`).
4. Push to the branch (`git push origin feature/your-feature`).
5. Open a pull request.

## License
This project is licensed under the MIT License. See the `LICENSE` file for details.

## Acknowledgments
- Built with open-source tools: Apache Spark, Kafka, Flask, and Docker.
- Inspired by real-world IoT energy management challenges.

---

For issues or questions, please open a GitHub issue or contact [your-email@example.com].
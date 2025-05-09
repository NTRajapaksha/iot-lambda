# Energy Forecasting with Lambda Architecture

This project implements a **Lambda architecture** to process and analyze energy consumption data from IoT devices. It combines **batch processing** for historical data analysis with **real-time streaming** for immediate insights, enabling accurate energy demand forecasting and anomaly detection. Built with scalable tools like **Apache Spark**, **Kafka**, **HDFS**, and **Flask**, the system provides a user-friendly dashboard and APIs to access results, making it valuable for utility companies and smart home users.

You can find a **sample demo video** here: https://drive.google.com/file/d/1WvP_hZAEpvfMvYdoNeJbT0s2JbA5uHGW/view?usp=sharing

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

![diagram-export-5-9-2025-12_28_12-PM](https://github.com/user-attachments/assets/7b98cea1-a71f-40df-b223-ee16807c6b64)


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
- A machine with at least 8GB RAM.

### Repository Structure
```
├── code/
│   ├── batch/            # Batch layer scripts (ingest.py, train.py)
│   ├── streaming/        # Streaming layer scripts (producer.py, detect.py)
│   ├── utils/            # Utility scripts (benchmark.py)
│   └── serving/          # Serving layer (app.py, templates/, static/)
├── config/               # Configuration files (Hadoop, Spark, Kafka)
├── data/                 # Shared data volume for Parquet, SQLite, etc.
├── hadoop/               # HDFS volumes (namenode, datanode)
├── spark-tmp/            # Temporary Spark files
└── docker-compose.yml    # Docker service definitions
```

### Steps to Run
1. **Clone the Repository**:
   ```bash
   git clone https://github.com/NTRajapaksha/iot-lambda.git
   cd energy-forecasting-lambda
   ```

2. **Prepare Data**:
   - Place your raw energy data CSV (e.g., `energy_data.csv`) in the `data/` directory.
   - You can find the dataset here: https://archive.ics.uci.edu/dataset/235/individual+household+electric+power+consumption
   - Ensure the CSV has columns like `Date`, `Time`, `Global_active_power`, etc., as expected by `ingest.py`.

3. **Start Docker Containers**:
   ```bash
   docker-compose up -d
   ```
   - Verify services are running:
     ```bash
     docker-compose ps
     ```

4. **Set Up Dependencies** (if not pre-installed):
   - Install Spark dependencies:
     ```bash
     docker exec -u root spark-master apt-get update
     docker exec -u root spark-master apt-get install -y sqlite3 python3-pip
     docker exec spark-master pip install kafka-python pandas>=1.0.5 scikit-learn pymongo redis matplotlib psutil
     ```
   - Install Kafka JAR for Spark Streaming:
     ```bash
     wget https://repo1.maven.org/maven2/org/apache/spark/spark-sql-kafka-0-10_2.12/3.5.0/spark-sql-kafka-0-10_2.12-3.5.0.jar -O spark-sql-kafka.jar
     docker cp spark-sql-kafka.jar spark-master:/opt/bitnami/spark/jars/spark-sql-kafka.jar
     ```
   - Install Kafka dependencies:
     ```bash
     docker exec -it --user root kafka apt-get update
     docker exec -it --user root kafka apt-get install -y python3 python3-pip
     docker exec -it --user root kafka pip3 install kafka-python
     ```

5. **Run Batch Processing**:
   - Copy batch scripts (if not mounted):
     ```bash
     docker cp ./code/batch/ingest.py spark-master:/app/code/batch/ingest.py
     docker cp ./code/batch/train.py spark-master:/app/code/batch/train.py
     docker cp ./code/utils/benchmark.py spark-master:/app/code/utils/benchmark.py
     ```
   - Run scripts:
     ```bash
     docker exec spark-master spark-submit /app/code/batch/ingest.py
     docker exec spark-master spark-submit /app/code/batch/train.py
     docker exec spark-master spark-submit /app/code/utils/benchmark.py
     ```

6. **Run Streaming Processing**:
   - Copy streaming scripts:
     ```bash
     docker exec -u 0 spark-master mkdir -p /app/code/streaming
     docker cp ./code/streaming/producer.py kafka:/app/code/streaming/producer.py
     docker cp ./code/streaming/detect.py spark-master:/app/code/streaming/detect.py
     ```
   - Start producer:
     ```bash
     docker exec kafka python3 /app/code/streaming/producer.py
     ```
   - Start anomaly detection:
     ```bash
     docker exec spark-master spark-submit --packages org.apache.spark:spark-sql-kafka-0-10_2.12:3.5.0 /app/code/streaming/detect.py
     ```

7. **Set Up Serving Layer**:
   - Copy Flask files:
     ```bash
     docker cp ./code/serving/app.py flask:/app/app.py
     docker cp ./code/serving/templates/dashboard.html flask:/app/templates/dashboard.html
     docker cp ./code/serving/templates/predict.html flask:/app/templates/predict.html
     docker cp ./code/serving/requirements.txt flask:/app/requirements.txt
     ```
   - Install dependencies:
     ```bash
     docker exec flask pip install --no-cache-dir -r /app/requirements.txt
     docker exec -it flask apt-get update
     docker exec -it flask apt-get install -y sqlite3
     ```
   - Rebuild and restart Flask:
     ```bash
     docker-compose up -d --build flask
     ```

8. **Access the Dashboard**:
   - Open `http://localhost:5000` to view the Flask dashboard.
   - Check APIs (e.g., `http://localhost:5000/api/anomalies`).
   - Verify Flask logs:
     ```bash
     docker logs flask
     ```

9. **Monitor Services**:
   - HDFS Web UI: `http://localhost:9870`
   - Spark Master UI: `http://localhost:8080`
   - Kafka: `kafka:9092` (within Docker network).

### Stopping the Services
```bash
docker-compose down
```

## Troubleshooting
- **HDFS Safemode**:
  ```bash
  docker exec namenode hdfs dfsadmin -safemode leave
  ```
- **Permission Issues**:
  ```bash
  docker exec -u 0 spark-master chmod -R 755 /app/code
  docker exec -u 0 flask chmod -R 755 /app
  ```
- **Network Connectivity**:
  ```bash
  docker exec spark-master ping namenode -c 4
  docker exec spark-worker ping spark-master -c 4
  ```
- **Verify Outputs**:
  ```bash
  docker exec spark-master sqlite3 /app/data/energy_forecasting.db "SELECT * FROM anomalies LIMIT 5;"
  docker exec spark-master sqlite3 /app/data/energy_forecasting.db "SELECT * FROM forecasts LIMIT 5;"
  docker exec namenode hdfs dfs -ls /input/energy_data
  ```
- **Reset Database**:
  ```bash
  docker exec -it flask sqlite3 /app/data/energy_forecasting.db
  DROP TABLE IF EXISTS forecasts;
  DROP TABLE IF EXISTS model_info;
  DROP TABLE IF EXISTS training_runs;
  .exit
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

## Acknowledgments
- Built with open-source tools: Apache Spark, Kafka, Flask, and Docker.
- Inspired by real-world IoT energy management challenges.

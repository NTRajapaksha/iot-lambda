# Energy Forecasting with Lambda Architecture

[... Previous sections unchanged ...]

## Setup and Installation

### Prerequisites
- **Docker** and **Docker Compose** installed.
- A machine with at least 8GB RAM (tested on a 20GB RAM system with Intel i3 CPU).

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
     docker cp ./code/batch/benchmark.py spark-master:/app/code/batch/benchmark.py
     ```
   - Run scripts:
     ```bash
     docker exec spark-master spark-submit /app/code/batch/ingest.py
     docker exec spark-master spark-submit /app/code/batch/train.py
     docker exec spark-master spark-submit /app/code/batch/benchmark.py
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

[... Rest of the README unchanged ...]
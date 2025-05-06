from pyspark.sql import SparkSession
from pyspark.ml.regression import RandomForestRegressor
from pyspark.ml.feature import VectorAssembler, StandardScaler
from pyspark.ml.evaluation import RegressionEvaluator
from pyspark.sql.functions import hour, dayofweek, month, col, count, when, avg, lit, isnan, current_timestamp
from pyspark.sql.types import DoubleType
import logging
import json
import os
import sys
import sqlite3
import pandas as pd
from datetime import datetime

# Configure main logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("/app/data/train.log")
    ]
)
logger = logging.getLogger("EnergyForecasting")

# Create a separate logger for metrics
metrics_logger = logging.getLogger("ModelMetrics")
metrics_logger.setLevel(logging.INFO)

# Define paths
BASE_DIR = "/app/data"
LOGS_DIR = os.path.join(BASE_DIR, "logs")
METRICS_LOG_PATH = os.path.join(LOGS_DIR, "model_metrics.jsonl")
SQLITE_DB_PATH = os.path.join(BASE_DIR, "energy_forecasting.db")

# Ensure directories exist
os.makedirs(LOGS_DIR, exist_ok=True)

# Add file handler for metrics logger
metrics_file_handler = logging.FileHandler(METRICS_LOG_PATH)
metrics_file_handler.setFormatter(logging.Formatter('%(message)s'))
metrics_logger.addHandler(metrics_file_handler)


def log_metrics(metrics_dict):
    metrics_logger.info(json.dumps(metrics_dict))


def ensure_sqlite_db(db_path):
    try:
        db_dir = os.path.dirname(db_path)
        if db_dir and not os.path.exists(db_dir):
            os.makedirs(db_dir)

        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS forecasts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            active_power REAL,
            prediction REAL,
            created_at TEXT
        )
        ''')
        logger.info("Created or verified forecasts table")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS model_info (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_timestamp TEXT,
            rmse REAL,
            r2 REAL,
            mae REAL,
            mse REAL,
            coefficients TEXT,
            intercept REAL,
            feature_columns TEXT,
            num_training_samples INTEGER,
            created_at TEXT
        )
        ''')
        logger.info("Created or verified model_info table")

        cursor.execute('''
        CREATE TABLE IF NOT EXISTS training_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_timestamp TEXT,
            status TEXT,
            duration_seconds REAL,
            config TEXT,
            error_message TEXT,
            created_at TEXT
        )
        ''')
        logger.info("Created or verified training_runs table")

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Error setting up SQLite database: {str(e)}")
        return False


def train_model():
    start_time = datetime.now()
    timestamp = start_time.strftime("%Y%m%d_%H%M%S")

    logger.info("Initializing Spark session...")
    spark = SparkSession.builder \
        .appName(f"EnergyForecasting_{timestamp}") \
        .config("spark.hadoop.fs.defaultFS", "hdfs://namenode:9000") \
        .config("spark.driver.memory", "4g") \
        .config("spark.executor.memory", "4g") \
        .config("spark.sql.shuffle.partitions", "200") \
        .getOrCreate()

    spark.sparkContext._jsc.hadoopConfiguration().set(
        "fs.defaultFS", "hdfs://namenode:9000")
    spark.sparkContext._jsc.hadoopConfiguration().set(
        "dfs.client.use.datanode.hostname", "true")

    if not ensure_sqlite_db(SQLITE_DB_PATH):
        logger.warning("Failed to set up SQLite database. Using CSV fallback.")

    logger.info("Loading data...")
    df = None
    data_paths = [
        "hdfs://namenode:9000/input/energy_data",
        "/tmp/energy_data",
        "file:///tmp/energy_data"
    ]

    for path in data_paths:
        try:
            logger.info(f"Attempting to load data from {path}")
            df = spark.read.parquet(path)
            logger.info(f"Successfully loaded data from {path}")
            break
        except Exception as e:
            logger.warning(f"Failed to load data from {path}: {str(e)}")

    if df is None:
        logger.error("Failed to load data from any location. Exiting.")
        log_training_run(timestamp, "FAILED", start_time,
                         None, "Failed to load data")
        return

    row_count = df.count()
    logger.info(f"Loaded dataset with {row_count} rows")
    if row_count == 0:
        logger.error("Dataset is empty. Exiting.")
        log_training_run(timestamp, "FAILED", start_time,
                         None, "Dataset is empty")
        return

    df.printSchema()

    numeric_columns = ["Voltage", "current", "active_power", "reactive_power",
                       "Sub_metering_1", "Sub_metering_2", "Sub_metering_3"]
    required_columns = ["active_power", "timestamp"] + numeric_columns

    missing_columns = [c for c in required_columns if c not in df.columns]
    if missing_columns:
        logger.error(f"Required columns missing: {missing_columns}")
        for col_name in missing_columns:
            logger.warning(f"Creating dummy column {col_name} with zeros")
            df = df.withColumn(col_name, lit(0.0))

    logger.info("Cleaning data...")
    for col_name in numeric_columns:
        if col_name in df.columns:
            df = df.withColumn(col_name, col(col_name).cast(DoubleType()))
            if col_name in ["active_power", "Sub_metering_1", "Sub_metering_2", "Sub_metering_3"]:
                df = df.withColumn(col_name,
                                   when(col(col_name).isNull() |
                                        isnan(col(col_name)), 0.0)
                                   .otherwise(col(col_name)))
            elif col_name == "Voltage":
                df = df.withColumn(col_name,
                                   when(col(col_name).isNull() |
                                        isnan(col(col_name)), 230.0)
                                   .otherwise(col(col_name)))
            else:  # current, reactive_power
                mean_val = df.select(avg(col(col_name))).first()[0] or 0.0
                df = df.withColumn(col_name,
                                   when(col(col_name).isNull() |
                                        isnan(col(col_name)), mean_val)
                                   .otherwise(col(col_name)))

    if "timestamp" in df.columns:
        if "timestamp" not in df.schema["timestamp"].dataType.typeName().lower():
            logger.warning("Converting timestamp column...")
            df = df.withColumn("timestamp", col("timestamp").cast("timestamp"))
    else:
        logger.warning("Creating dummy timestamp column")
        df = df.withColumn("timestamp", current_timestamp())

    logger.info("Performing feature engineering...")
    df = df.withColumn("hour", hour(df.timestamp)) \
           .withColumn("day_of_week", dayofweek(df.timestamp)) \
           .withColumn("month", month(df.timestamp))

    potential_feature_cols = ["Voltage", "current", "Sub_metering_1",
                              "Sub_metering_2", "Sub_metering_3", "hour", "day_of_week", "month"]
    available_feature_cols = [
        c for c in potential_feature_cols if c in df.columns]
    logger.info(f"Using features: {', '.join(available_feature_cols)}")

    if len(available_feature_cols) < 2:
        logger.error("Not enough features. Exiting.")
        log_training_run(timestamp, "FAILED", start_time,
                         None, "Not enough features")
        return

    assembler = VectorAssembler(
        inputCols=available_feature_cols, outputCol="features_raw", handleInvalid="skip")
    scaler = StandardScaler(inputCol="features_raw",
                            outputCol="features", withStd=True, withMean=True)

    try:
        logger.info("Sample data before assembly:")
        df.show(5, truncate=False)
        for col_name in potential_feature_cols:
            null_count = df.filter(col(col_name).isNull() |
                           isnan(col(col_name))).count()
            logger.info(f"Null count in {col_name}: {null_count}")

        logger.info("Transforming features...")
        df_assembled = assembler.transform(df)
        df_assembled = df_assembled.na.drop(subset=["features_raw"])
        row_count = df_assembled.count()
        logger.info(f"Rows after assembling features: {row_count}")
        if row_count == 0:
            logger.error("No valid data after feature assembly. Exiting.")
            log_training_run(timestamp, "FAILED", start_time, None,
                     "No valid data after feature assembly")
            return
        transformed_df = scaler.fit(df_assembled).transform(df_assembled)
    except Exception as e:
        logger.error(f"Feature transformation failed: {str(e)}")
        logger.error(f"Data sample: {df_assembled.show(5, truncate=False)}")
        logger.error("Exiting due to feature transformation failure.")
        log_training_run(timestamp, "FAILED", start_time, None,
                         f"Feature transformation failed: {str(e)}")
        return

    train, test = transformed_df.randomSplit([0.8, 0.2], seed=42)
    logger.info(f"Training set: {train.count()}, Test set: {test.count()}")

    logger.info("Training Random Forest model...")
    model_configs = [
        {"numTrees": 50, "maxDepth": 10},
        {"numTrees": 30, "maxDepth": 5},
        {"numTrees": 100, "maxDepth": 15}
    ]

    model = None
    successful_config = None
    for config in model_configs:
        try:
            rf = RandomForestRegressor(
                featuresCol="features", labelCol="active_power", **config)
            model = rf.fit(train)
            successful_config = config
            logger.info("Model trained successfully!")
            break
        except Exception as e:
            logger.warning(f"Training failed with config {config}: {str(e)}")

    if model is None:
        logger.error("All model configs failed. Exiting.")
        log_training_run(timestamp, "FAILED", start_time,
                         None, "All configs failed")
        return

    predictions = model.transform(test)
    evaluator = RegressionEvaluator(
        labelCol="active_power", predictionCol="prediction")
    metrics = {
        "rmse": evaluator.evaluate(predictions, {evaluator.metricName: "rmse"}),
        "r2": evaluator.evaluate(predictions, {evaluator.metricName: "r2"}),
        "mae": evaluator.evaluate(predictions, {evaluator.metricName: "mae"}),
        "mse": evaluator.evaluate(predictions, {evaluator.metricName: "mse"})
    }
    for metric, value in metrics.items():
        logger.info(f"{metric.upper()}: {value}")

    metrics_dict = {
        "model_timestamp": timestamp,
        "rmse": float(metrics["rmse"]),
        "r2": float(metrics["r2"]),
        "mae": float(metrics["mae"]),
        "mse": float(metrics["mse"]),
        "train_samples": train.count(),
        "test_samples": test.count(),
        "features": available_feature_cols,
        "config": successful_config,
        "timestamp": datetime.now().isoformat()
    }
    log_metrics(metrics_dict)

    model_locations = [
        f"hdfs://namenode:9000/models/energy_model_{timestamp}",
        f"/tmp/energy_model_{timestamp}"
    ]
    for location in model_locations:
        try:
            model.write().overwrite().save(location)
            logger.info(f"Model saved to {location}")
        except Exception as e:
            logger.warning(f"Failed to save model to {location}: {str(e)}")

    try:
        logger.info("Saving predictions to SQLite...")
        pred_pd = predictions.select(
            "timestamp", "active_power", "prediction").limit(1000).toPandas()
        pred_pd["timestamp"] = pred_pd["timestamp"].astype(str)
        pred_pd["created_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        conn = sqlite3.connect(SQLITE_DB_PATH)
        pred_pd.to_sql("forecasts", conn, if_exists="append", index=False)

        model_info = {
            "model_timestamp": timestamp,
            "rmse": metrics["rmse"],
            "r2": metrics["r2"],
            "mae": metrics["mae"],
            "mse": metrics["mse"],
            "feature_columns": json.dumps(available_feature_cols),
            "num_training_samples": train.count(),
            "created_at": pred_pd["created_at"][0]
        }
        pd.DataFrame([model_info]).to_sql(
            "model_info", conn, if_exists="append", index=False)

        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM forecasts")
        logger.info(f"Forecasts table rows: {cursor.fetchone()[0]}")
        cursor.execute("SELECT COUNT(*) FROM model_info")
        logger.info(f"Model_info table rows: {cursor.fetchone()[0]}")

        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"SQLite save failed: {str(e)}. Saving to CSV...")
        predictions.select("timestamp", "active_power", "prediction") \
            .coalesce(1) \
            .write \
            .mode("overwrite") \
            .csv(f"/tmp/predictions_{timestamp}")

    log_training_run(timestamp, "SUCCESS", start_time, successful_config)
    logger.info("Training completed successfully")
    return model


def log_training_run(model_timestamp, status, start_time, config=None, error_message=None):
    try:
        duration = (datetime.now() - start_time).total_seconds()
        conn = sqlite3.connect(SQLITE_DB_PATH)
        cursor = conn.cursor()
        cursor.execute('''
        INSERT INTO training_runs (model_timestamp, status, duration_seconds, config, error_message, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ''', (model_timestamp, status, duration, json.dumps(config) if config else None, error_message, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log training run: {str(e)}")


if __name__ == "__main__":
    try:
        train_model()
    except Exception as e:
        logger.error(f"Main process crashed: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        log_training_run(datetime.now().strftime("%Y%m%d_%H%M%S"),
                         "CRASHED", datetime.now(), None, str(e))

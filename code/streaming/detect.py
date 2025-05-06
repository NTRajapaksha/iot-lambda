from pyspark.sql import SparkSession
from pyspark.sql.functions import *
from pyspark.sql.types import *
import logging
import sys
import os
import time
from datetime import datetime
import traceback

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def create_spark_session():
    logger.info("Initializing Spark session...")
    try:
        os.environ['HADOOP_HOME'] = ''
        os.environ['HADOOP_CONF_DIR'] = ''
        spark = SparkSession.builder \
            .appName("AnomalyDetection") \
            .config("spark.hadoop.fs.defaultFS", "file:///") \
            .config("spark.hadoop.fs.hdfs.impl", "org.apache.hadoop.fs.LocalFileSystem") \
            .config("spark.hadoop.fs.file.impl", "org.apache.hadoop.fs.LocalFileSystem") \
            .config("spark.hadoop.security.authentication", "simple") \
            .config("spark.driver.extraJavaOptions", "-Dhadoop.home.dir=") \
            .config("spark.executor.extraJavaOptions", "-Dhadoop.home.dir=") \
            .getOrCreate()
        spark.sparkContext.setLogLevel("WARN")
        logger.info("Spark session initialized successfully")
        return spark
    except Exception as e:
        logger.error(f"Failed to initialize Spark session: {str(e)}")
        raise


def ensure_sqlite_db(db_path):
    """Create SQLite database and anomalies table"""
    try:
        import sqlite3
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.execute('''
        CREATE TABLE IF NOT EXISTS anomalies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp BIGINT,
            device_id TEXT,
            active_power REAL,
            reactive_power REAL,
            voltage INTEGER,
            current REAL,
            Sub_metering_1 REAL,
            Sub_metering_2 REAL,
            Sub_metering_3 REAL,
            is_anomaly BOOLEAN,
            is_injected_anomaly BOOLEAN,
            mean REAL,
            stddev REAL,
            lower_bound REAL,
            upper_bound REAL,
            detection_time TIMESTAMP
        )
        ''')
        conn.commit()
        conn.close()
        logger.info(f"SQLite database initialized at {db_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to initialize SQLite database: {str(e)}")
        return False


def get_kafka_data(spark, bootstrap_servers="kafka:9092", topic="energy-stream"):
    logger.info(
        f"Connecting to Kafka at {bootstrap_servers} with topic {topic}")
    schema = StructType([
        StructField("timestamp", LongType()),
        StructField("device_id", StringType()),
        StructField("active_power", DoubleType()),
        StructField("reactive_power", DoubleType()),
        StructField("voltage", IntegerType()),
        StructField("current", DoubleType()),
        StructField("Sub_metering_1", DoubleType()),
        StructField("Sub_metering_2", DoubleType()),
        StructField("Sub_metering_3", DoubleType()),
        StructField("is_injected_anomaly", BooleanType())
    ])
    try:
        kafka_df = spark.readStream \
            .format("kafka") \
            .option("kafka.bootstrap.servers", bootstrap_servers) \
            .option("subscribe", topic) \
            .load()
        parsed_df = kafka_df.select(from_json(col("value").cast(
            "string"), schema).alias("data")).select("data.*")
        logger.info("Connected to Kafka using Spark Kafka integration")
        return parsed_df, "kafka"
    except Exception as kafka_error:
        logger.error(f"Failed to connect to Kafka: {str(kafka_error)}")
        test_data = [(int(time.time()), "meter_1", 2.5,
                      0.5, 230, 0.01, 0.5, 0.3, 0.2, False)]
        test_df = spark.createDataFrame(test_data, schema)
        rate_df = spark.readStream \
            .format("rate") \
            .option("rowsPerSecond", 1) \
            .load() \
            .withColumnRenamed("timestamp", "rate_timestamp")
        logger.info("Set up simulation data source as fallback")
        return rate_df.crossJoin(test_df.limit(1)), "simulation"


def process_batch(batch_df, batch_id, db_path):
    """Process batch to detect anomalies using 3-sigma rule"""
    batch_start_time = datetime.now()
    logger.info(
        f"Processing batch {batch_id} started at {batch_start_time}...")

    try:
        if batch_df.count() == 0:
            logger.info(f"Batch {batch_id}: No data received")
            return

        device_stats = batch_df.groupBy("device_id").agg(
            avg("active_power").alias("mean"),
            stddev("active_power").alias("stddev"),
            count("active_power").alias("count")
        ).filter(col("count") >= 3)

        if device_stats.count() == 0:
            logger.info(
                f"Batch {batch_id}: No devices with sufficient data points")
            return

        enriched_df = batch_df.join(
            broadcast(device_stats), on="device_id", how="inner")
        anomaly_df = enriched_df.withColumn(
            "lower_bound", col("mean") - (3 * col("stddev"))
        ).withColumn(
            "upper_bound", col("mean") + (3 * col("stddev"))
        ).withColumn(
            "is_anomaly",
            (col("active_power") < col("lower_bound")) | (
                col("active_power") > col("upper_bound"))
        ).withColumn(
            "detection_time", current_timestamp()
        ).filter(
            col("is_anomaly") | col("is_injected_anomaly")
        )

        anomaly_count = anomaly_df.count()
        logger.info(f"Batch {batch_id}: Detected {anomaly_count} anomalies")

        if anomaly_count > 0:
            try:
                import pandas as pd
                pandas_df = anomaly_df.select(
                    "timestamp", "device_id", "active_power", "reactive_power", "voltage",
                    "current", "Sub_metering_1", "Sub_metering_2", "Sub_metering_3",
                    "is_anomaly", "is_injected_anomaly", "mean", "stddev", "lower_bound",
                    "upper_bound", "detection_time"
                ).toPandas()
                import sqlite3
                conn = sqlite3.connect(db_path)
                pandas_df.to_sql('anomalies', conn,
                                 if_exists='append', index=False)
                conn.commit()
                conn.close()
                logger.info(
                    f"Batch {batch_id}: Saved {anomaly_count} anomalies to SQLite")
                for row in anomaly_df.limit(3).collect():
                    logger.info(
                        f"  Device: {row['device_id']}, Power: {row['active_power']}, Bounds: [{row['lower_bound']:.2f}, {row['upper_bound']:.2f}]")
            except Exception as sql_error:
                logger.error(f"Error saving to SQLite: {str(sql_error)}")
                # Use local file output as fallback
                output_path = f"/app/data/anomalies_batch_{batch_id}_{int(time.time())}.json"
                try:
                    pandas_df = anomaly_df.toPandas()
                    pandas_df.to_json(
                        output_path, orient='records', lines=True)
                    logger.info(
                        f"Saved anomalies to local JSON file {output_path} as fallback")
                except Exception as json_error:
                    logger.error(f"Error saving to JSON: {str(json_error)}")

    except Exception as e:
        logger.error(f"Error processing batch {batch_id}: {str(e)}")
        logger.error(traceback.format_exc())

    batch_duration = (datetime.now() - batch_start_time).total_seconds()
    logger.info(
        f"Batch {batch_id} processing completed in {batch_duration:.2f} seconds")


def detect_anomalies():
    """Detect anomalies in streaming energy data"""
    # Explicitly disable Hadoop
    os.environ['HADOOP_HOME'] = ''
    os.environ['HADOOP_CONF_DIR'] = ''

    # Ensure data directory exists for local file operations
    os.makedirs("/app/data", exist_ok=True)
    os.makedirs("/app/data/checkpoints", exist_ok=True)

    # Clean up checkpoint directory
    import shutil
    checkpoint_dir = "/app/data/checkpoints"
    shutil.rmtree(checkpoint_dir, ignore_errors=True)
    os.makedirs(checkpoint_dir, exist_ok=True)

    # Database path
    db_path = "/app/data/energy_forecasting.db"
    sqlite_available = ensure_sqlite_db(db_path)
    if not sqlite_available:
        logger.warning(
            "SQLite database initialization failed. Using local JSON files as fallback.")

    spark = create_spark_session()
    logger.info("Starting anomaly detection stream processing...")

    try:
        df, source_type = get_kafka_data(spark)
        logger.info(f"Using data source: {source_type}")

        # Use parsed DataFrame directly; no need to re-parse
        streaming_df = df

        # Add is_injected_anomaly if missing (for simulation mode)
        if "is_injected_anomaly" not in streaming_df.columns:
            streaming_df = streaming_df.withColumn(
                "is_injected_anomaly", lit(False))

        # Add timestamp_dt for processing
        streaming_df = streaming_df.withColumn(
            "timestamp_dt", from_unixtime(col("timestamp")).cast("timestamp"))

        # Use local filesystem for checkpointing
        checkpoint_path = f"/tmp/checkpoints/anomaly_detection_{int(time.time())}"

        # Process in micro-batches
        query = streaming_df.writeStream \
            .foreachBatch(lambda df, id: process_batch(df, id, db_path)) \
            .outputMode("update") \
            .option("checkpointLocation", checkpoint_path) \
            .trigger(processingTime="10 seconds") \
            .start()

        logger.info(
            f"Streaming query started with checkpoint at {checkpoint_path}")
        query.awaitTermination()

    except Exception as e:
        logger.error(f"Error in streaming process: {str(e)}")
        logger.error(traceback.format_exc())
        raise


if __name__ == "__main__":
    # Set Hadoop-related environment variables to empty to disable HDFS
    os.environ['HADOOP_HOME'] = ''
    os.environ['HADOOP_CONF_DIR'] = ''
    os.environ['hadoop.home.dir'] = ''

    retry_count = 0
    max_retries = 5
    retry_delay = 15
    while retry_count < max_retries:
        try:
            logger.info(
                f"Starting anomaly detection (attempt {retry_count + 1}/{max_retries})")
            detect_anomalies()
            break
        except Exception as e:
            retry_count += 1
            logger.error(f"Unhandled exception in main process: {str(e)}")
            logger.error(traceback.format_exc())
            if retry_count < max_retries:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
                retry_delay *= 1.5
            else:
                logger.error(f"Failed after {max_retries} attempts, exiting.")
                sys.exit(1)

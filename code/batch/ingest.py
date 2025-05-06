from pyspark.sql import SparkSession
from pyspark.sql.functions import to_timestamp, concat_ws, col, when, isnan, lag, lit, expr, hour, dayofweek, month, avg, stddev, min, max, monotonically_increasing_id
from pyspark.sql.types import DoubleType
from pyspark.sql.window import Window
import logging
import sys
import os
import json

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("DataIngestion")


def main():
    logger.info("Initializing Spark session with HDFS configuration...")
    # Create Spark session with HDFS configuration
    spark = SparkSession.builder \
        .appName("DataIngestion") \
        .config("spark.hadoop.fs.defaultFS", "hdfs://namenode:9000") \
        .config("spark.driver.memory", "2g") \
        .config("spark.executor.memory", "2g") \
        .config("spark.sql.shuffle.partitions", "200") \
        .getOrCreate()

    # Configure Hadoop settings
    spark.sparkContext._jsc.hadoopConfiguration().set(
        "fs.defaultFS", "hdfs://namenode:9000")
    spark.sparkContext._jsc.hadoopConfiguration().set(
        "dfs.client.use.datanode.hostname", "true")

    # Define data paths
    input_path = "file:/app/data/energy_data.csv"
    hdfs_output_path = "hdfs://namenode:9000/input/energy_data"
    local_output_path = "/tmp/energy_data"
    stats_output_path = "/app/data/energy_statistics.json"

    logger.info(f"Reading energy data from {input_path}...")

    try:
        # Load UCI dataset from local storage
        df = spark.read.csv(input_path,
                            sep=",", header=True, inferSchema=True,
                            nullValue="?", mode="DROPMALFORMED")
    except Exception as e:
        logger.error(f"Error reading CSV file: {str(e)}")
        try:
            logger.info("Trying alternative separator (comma)...")
            df = spark.read.csv(input_path,
                                sep=",", header=True, inferSchema=True,
                                nullValue="?", mode="DROPMALFORMED")
        except Exception as e2:
            logger.error(f"Failed to read CSV with comma separator: {str(e2)}")
            logger.error("Exiting due to data read failure")
            return

    # Check if data was loaded successfully
    if df.count() == 0:
        logger.error(
            "Dataset is empty. Please check the file path and content.")
        return

    # Remove the '_c0' column if it exists
    if '_c0' in df.columns:
        df = df.drop('_c0')

    logger.info(f"Available columns: {df.columns}")

    # Check expected column names based on UCI dataset
    expected_columns = ["Date", "Time", "Global_active_power", "Global_reactive_power",
                        "Voltage", "Global_intensity", "Sub_metering_1", "Sub_metering_2",
                        "Sub_metering_3"]

    missing_columns = [
        col for col in expected_columns if col not in df.columns]
    if missing_columns:
        logger.warning(f"Missing expected columns: {missing_columns}")
        alternative_names = {
            "Date": ["date"],
            "Time": ["time"],
            "Global_active_power": ["active_power", "globalactivepower"],
            "Global_reactive_power": ["reactive_power", "globalreactivepower"],
            "Voltage": ["voltage"],
            "Global_intensity": ["current", "globalintensity", "intensity"],
            "Sub_metering_1": ["submetering1", "sub_metering1"],
            "Sub_metering_2": ["submetering2", "sub_metering2"],
            "Sub_metering_3": ["submetering3", "sub_metering3"]
        }

        # Attempt to rename columns based on alternatives
        for expected, alternatives in alternative_names.items():
            if expected not in df.columns:
                for alt in alternatives:
                    if alt in df.columns:
                        df = df.withColumnRenamed(alt, expected)
                        logger.info(f"Renamed column '{alt}' to '{expected}'")
                        break

    # Verify if Date and Time columns exist before processing
    if "Date" in df.columns and "Time" in df.columns:
        df = df.withColumn("timestamp",
                       to_timestamp(concat_ws(" ", col("Date"), col("Time")), "dd/MM/yyyy HH:mm:ss"))
        # Add a row index to infer missing timestamps
        df = df.withColumn("row_id", monotonically_increasing_id())
        # Find the first valid timestamp
        first_valid_ts_row = df.filter(col("timestamp").isNotNull()).orderBy("row_id").select("timestamp", "row_id").first()
        if first_valid_ts_row:
            # Convert timestamp to string
            first_valid_ts = first_valid_ts_row["timestamp"].strftime("%Y-%m-%d %H:%M:%S")
            first_row_id = first_valid_ts_row["row_id"]
        else:
            first_valid_ts = "2006-12-16 17:24:00"
            first_row_id = 0
            # Calculate minutes difference from the first valid timestamp
            df = df.withColumn("minutes_diff", (col("row_id") -
                       first_row_id) * 60)  # 1 minute per row
            # Fill NULL timestamps by adding minutes to the first valid timestamp
            df = df.withColumn("timestamp",
                       when(col("timestamp").isNull(),
                            expr(f"timestampadd(MINUTE, minutes_diff, to_timestamp('{first_valid_ts}', 'yyyy-MM-dd HH:mm:ss'))"))
                       .otherwise(col("timestamp")))
            df = df.drop("row_id", "minutes_diff")
    else:
        logger.error("Required Date and/or Time columns not found in dataset.")
        return

    # Convert string columns to numeric and handle missing values
    numeric_columns = ["Global_active_power", "Global_reactive_power", "Voltage",
                       "Global_intensity", "Sub_metering_1", "Sub_metering_2", "Sub_metering_3"]

    for column_name in numeric_columns:
        if column_name in df.columns:
            df = df.withColumn(column_name, col(
                column_name).cast(DoubleType()))
            # Replace nulls with zeros for power-related columns, default for Voltage, mean for current
            if column_name in ["Global_active_power", "Sub_metering_1", "Sub_metering_2", "Sub_metering_3"]:
                df = df.withColumn(column_name,
                                   when(col(column_name).isNull() |
                                        isnan(col(column_name)), 0.0)
                                   .otherwise(col(column_name)))
            elif column_name == "Voltage":
                df = df.withColumn(column_name,
                                   when(col(column_name).isNull() |
                                        isnan(col(column_name)), 230.0)
                                   .otherwise(col(column_name)))
            else:  # Global_intensity (current), Global_reactive_power
                mean_val = df.select(col(column_name).cast("double")).agg(
                    {column_name: "avg"}).collect()[0][0] or 0.0
                df = df.withColumn(column_name,
                                   when(col(column_name).isNull() |
                                        isnan(col(column_name)), mean_val)
                                   .otherwise(col(column_name)))

    # Select relevant columns and rename for clarity
    try:
        df = df.select(
            "timestamp",
            col("Global_active_power").alias("active_power"),
            col("Global_reactive_power").alias("reactive_power"),
            "Voltage",
            col("Global_intensity").alias("current"),
            "Sub_metering_1",
            "Sub_metering_2",
            "Sub_metering_3"
        )
    except Exception as e:
        logger.error(f"Error selecting columns: {str(e)}")
        available_cols = df.columns
        select_cols = []
        if "timestamp" in available_cols:
            select_cols.append("timestamp")

        column_mapping = {
            "active_power": ["Global_active_power", "active_power"],
            "reactive_power": ["Global_reactive_power", "reactive_power"],
            "Voltage": ["Voltage", "voltage"],
            "current": ["Global_intensity", "current"],
            "Sub_metering_1": ["Sub_metering_1", "submetering_1"],
            "Sub_metering_2": ["Sub_metering_2", "sub_metering_2"],
            "Sub_metering_3": ["Sub_metering_3", "sub_metering_3"]
        }

        for target, sources in column_mapping.items():
            found = False
            for source in sources:
                if source in available_cols:
                    select_cols.append(col(source).alias(target))
                    found = True
                    break
            if not found:
                logger.warning(f"Creating dummy column for {target}")
                from pyspark.sql.functions import lit
                select_cols.append(lit(0.0).alias(target))

        df = df.select(*select_cols)

    # Check if we have data after transformations
    row_count = df.count()
    logger.info(f"Row count after transformations: {row_count}")
    if row_count == 0:
        logger.error("No data available after transformations. Exiting.")
        return

    # Write to HDFS in Parquet format
    logger.info(f"Writing processed data to HDFS at {hdfs_output_path}...")
    try:
        df.write.mode("overwrite").parquet(hdfs_output_path)
        logger.info("Data successfully ingested to HDFS")
    except Exception as e:
        logger.error(f"Error writing to HDFS: {str(e)}")
        logger.info(f"Falling back to local storage at {local_output_path}...")
        try:
            df.write.mode("overwrite").parquet(local_output_path)
            logger.info(f"Data saved to local storage at {local_output_path}")
        except Exception as e2:
            logger.error(f"Error writing to local storage: {str(e2)}")
            try:
                csv_path = "/tmp/energy_data_csv"
                logger.info(f"Attempting to write as CSV to {csv_path}...")
                df.write.mode("overwrite").csv(csv_path, header=True)
                logger.info(f"Data saved as CSV to {csv_path}")
            except Exception as e3:
                logger.error(f"Failed to save data: {str(e3)}")

    # Show sample data
    logger.info("Sample of processed data:")
    df.show(5, truncate=False)

    # Display statistics
    logger.info(f"Total records processed: {row_count}")
    logger.info("Summary statistics:")
    df.describe().show()

    # Calculate and export statistics by hour of day
    logger.info("Calculating hourly statistics for producer calibration...")
    try:
        from pyspark.sql.functions import hour, dayofweek, month, avg, stddev, min, max

        df_with_time = df.withColumn("hour_of_day", hour("timestamp")) \
                         .withColumn("day_of_week", dayofweek("timestamp")) \
                         .withColumn("month", month("timestamp"))

        hourly_stats = df_with_time.groupBy("hour_of_day") \
            .agg(
                avg("active_power").alias("avg_active_power"),
                stddev("active_power").alias("stddev_active_power"),
                min("active_power").alias("min_active_power"),
                max("active_power").alias("max_active_power"),
                avg("voltage").alias("avg_voltage"),
                stddev("voltage").alias("stddev_voltage"),
                min("voltage").alias("min_voltage"),
                max("voltage").alias("max_voltage")
        ).orderBy("hour_of_day")

        weekday_stats = df_with_time.filter((col("day_of_week") >= 2) & (col("day_of_week") <= 6)) \
            .groupBy("hour_of_day") \
            .agg(avg("active_power").alias("weekday_avg_power")).orderBy("hour_of_day")

        weekend_stats = df_with_time.filter((col("day_of_week") == 1) | (col("day_of_week") == 7)) \
            .groupBy("hour_of_day") \
            .agg(avg("active_power").alias("weekend_avg_power")).orderBy("hour_of_day")

        winter_stats = df_with_time.filter((col("month") == 12) | (col("month") == 1) | (col("month") == 2)) \
            .agg(avg("active_power").alias("winter_avg_power"),
                 stddev("active_power").alias("winter_stddev_power"))

        summer_stats = df_with_time.filter((col("month") == 6) | (col("month") == 7) | (col("month") == 8)) \
            .agg(avg("active_power").alias("summer_avg_power"),
                 stddev("active_power").alias("summer_stddev_power"))

        other_season_stats = df_with_time.filter((col("month").isin(3, 4, 5, 9, 10, 11))) \
            .agg(avg("active_power").alias("other_season_avg_power"),
                 stddev("active_power").alias("other_season_stddev_power"))

        hourly_stats_list = [row.asDict() for row in hourly_stats.collect()]
        weekday_stats_list = [row.asDict() for row in weekday_stats.collect()]
        weekend_stats_list = [row.asDict() for row in weekend_stats.collect()]
        winter_stats_dict = winter_stats.collect(
        )[0].asDict() if winter_stats.count() > 0 else {}
        summer_stats_dict = summer_stats.collect(
        )[0].asDict() if summer_stats.count() > 0 else {}
        other_season_dict = other_season_stats.collect(
        )[0].asDict() if other_season_stats.count() > 0 else {}

        overall_stats = df.select(
            avg("active_power").alias("overall_avg_power"),
            stddev("active_power").alias("overall_stddev_power"),
            min("active_power").alias("overall_min_power"),
            max("active_power").alias("overall_max_power")
        ).collect()[0].asDict()

        statistics = {
            "hourly_stats": hourly_stats_list,
            "weekday_stats": weekday_stats_list,
            "weekend_stats": weekend_stats_list,
            "winter_stats": winter_stats_dict,
            "summer_stats": summer_stats_dict,
            "other_season_stats": other_season_dict,
            "overall_stats": overall_stats
        }

        os.makedirs(os.path.dirname(stats_output_path), exist_ok=True)
        with open(stats_output_path, 'w') as f:
            json.dump(statistics, f, indent=2)

        logger.info(f"Statistics successfully exported to {stats_output_path}")
    except Exception as e:
        logger.error(f"Error calculating statistics: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())

    logger.info("Data ingestion process completed")


if __name__ == "__main__":
    main()

from pyspark.ml.regression import RandomForestRegressionModel
from pyspark.ml import PipelineModel
import sqlite3
from flask import Flask, render_template, jsonify, request
from datetime import datetime
import logging
import os
import json
from pyspark.sql import SparkSession
from pyspark.ml.feature import VectorAssembler, StandardScaler
from pyspark.sql.functions import lit
from pyspark.sql.types import DoubleType, IntegerType

app = Flask(__name__)
DB_PATH = '/app/data/energy_forecasting.db'
MODEL_INFO_PATH = '/app/data/model_info.json'

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('/app/data/flask.log')
    ]
)
logger = logging.getLogger("EnergyDashboard")

# Initialize Spark session
spark = SparkSession.builder \
    .appName("EnergyPrediction") \
    .config("spark.hadoop.fs.defaultFS", "hdfs://namenode:9000") \
    .config("spark.driver.memory", "2g") \
    .config("spark.executor.memory", "2g") \
    .config("spark.sql.shuffle.partitions", "200") \
    .getOrCreate()

spark.sparkContext._jsc.hadoopConfiguration().set(
    "fs.defaultFS", "hdfs://namenode:9000")
spark.sparkContext._jsc.hadoopConfiguration().set(
    "dfs.client.use.datanode.hostname", "true")

# Global model variable
model = None
pipeline_model = None
feature_cols = ["Voltage", "current", "Sub_metering_1",
                "Sub_metering_2", "Sub_metering_3", "hour", "day_of_week", "month"]
model_info = None


def save_model_info(model_info_dict):
    """Save model information to a local JSON file for quick access"""
    try:
        with open(MODEL_INFO_PATH, 'w') as f:
            json.dump(model_info_dict, f)
        logger.info(f"Saved model info to {MODEL_INFO_PATH}")
    except Exception as e:
        logger.error(f"Failed to save model info: {str(e)}")


def load_model_info():
    """Load model information from local JSON file"""
    if os.path.exists(MODEL_INFO_PATH):
        try:
            with open(MODEL_INFO_PATH, 'r') as f:
                info = json.load(f)
            logger.info(f"Loaded model info from {MODEL_INFO_PATH}")
            return info
        except Exception as e:
            logger.error(f"Failed to load model info: {str(e)}")
    return None

# Load the latest model from HDFS


def load_latest_model():
    global model, pipeline_model, model_info

    try:
        # First try to load a pipeline model (preferred)
        hdfs_path = "hdfs://namenode:9000/models"
        model_files = spark.sparkContext._jvm.org.apache.hadoop.fs.FileSystem.get(
            spark.sparkContext._jsc.hadoopConfiguration()
        ).listStatus(spark.sparkContext._jvm.org.apache.hadoop.fs.Path(hdfs_path))

        pipeline_paths = [file.getPath().toString()
                          for file in model_files if "pipeline_model_" in file.getPath().getName()]
        model_paths = [file.getPath().toString()
                       for file in model_files if "energy_model_" in file.getPath().getName()]

        if pipeline_paths:
            latest_pipeline_path = max(pipeline_paths)
            pipeline_model = PipelineModel.load(latest_pipeline_path)
            logger.info(f"Loaded pipeline model from {latest_pipeline_path}")
            # Extract the RF model from the pipeline (last stage)
            model = pipeline_model.stages[-1]

            # Get model timestamp from path for model info lookup
            model_timestamp = latest_pipeline_path.split("_")[-1]

            # Try to load model info from database
            conn = get_db_connection()
            model_data = conn.execute('''
                SELECT * FROM model_info 
                WHERE model_timestamp = ? 
                ORDER BY created_at DESC LIMIT 1
            ''', (model_timestamp,)).fetchone()
            conn.close()

            if model_data:
                model_info = dict(model_data)
                save_model_info(model_info)
            else:
                model_info = load_model_info()

            return model
        elif model_paths:
            # Fallback to loading just the RF model
            latest_model_path = max(model_paths)
            model = RandomForestRegressionModel.load(latest_model_path)
            logger.info(f"Loaded model from {latest_model_path}")
            model_info = load_model_info()
            return model
        else:
            logger.error("No models found in HDFS /models directory")
            return None
    except Exception as e:
        logger.error(f"Failed to load model: {str(e)}")
        return None


# Load model on startup
model = load_latest_model()


def get_db_connection():
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn
    except sqlite3.Error as e:
        logger.error(f"Database connection failed: {str(e)}")
        raise


def validate_input(form_data):
    """Validate and clean input data from the prediction form"""
    errors = []
    values = {}

    try:
        voltage = float(form_data.get('Voltage', 0))
        if voltage < 0 or voltage > 500:  # Reasonable range for voltage
            errors.append("Voltage should be between 0 and 500V")
        values['Voltage'] = voltage
    except ValueError:
        errors.append("Voltage must be a valid number")
        values['Voltage'] = 0

    try:
        current = float(form_data.get('current', 0))
        if current < 0 or current > 100:  # Reasonable range for current
            errors.append("Current should be between 0 and 100A")
        values['current'] = current
    except ValueError:
        errors.append("Current must be a valid number")
        values['current'] = 0

    # Validate the sub metering values
    for i in range(1, 4):
        field = f'Sub_metering_{i}'
        try:
            value = float(form_data.get(field, 0))
            if value < 0:
                errors.append(f"{field} should be a positive value")
            values[field] = value
        except ValueError:
            errors.append(f"{field} must be a valid number")
            values[field] = 0

    # Time-related fields
    try:
        hour = int(form_data.get('hour', 0))
        if hour < 0 or hour > 23:
            errors.append("Hour should be between 0 and 23")
        values['hour'] = hour
    except ValueError:
        errors.append("Hour must be a valid integer")
        values['hour'] = 0

    try:
        day_of_week = int(form_data.get('day_of_week', 1))
        if day_of_week < 1 or day_of_week > 7:
            errors.append("Day of week should be between 1 and 7")
        values['day_of_week'] = day_of_week
    except ValueError:
        errors.append("Day of week must be a valid integer")
        values['day_of_week'] = 1

    try:
        month = int(form_data.get('month', 1))
        if month < 1 or month > 12:
            errors.append("Month should be between 1 and 12")
        values['month'] = month
    except ValueError:
        errors.append("Month must be a valid integer")
        values['month'] = 1

    return values, errors


@app.route('/')
def dashboard():
    try:
        conn = get_db_connection()
        anomalies = conn.execute('''
            SELECT device_id, detection_time AS timestamp, active_power AS observed_value,
                   mean AS mean_value, stddev AS stddev_value
            FROM anomalies
            ORDER BY detection_time DESC
            
        ''').fetchall()
        forecasts = conn.execute('''
            SELECT timestamp, active_power, prediction
            FROM forecasts
            ORDER BY timestamp DESC
            LIMIT 100
        ''').fetchall()
        model_info = conn.execute('''
            SELECT rmse, r2, mae, created_at
            FROM model_info
            ORDER BY created_at DESC
            LIMIT 1
        ''').fetchone()

        def format_time(record):
            ts = record['timestamp']
            try:
                ts = datetime.fromisoformat(ts.replace(
                    'Z', '+00:00')).strftime('%Y-%m-%d %H:%M:%S')
            except:
                ts = ts
            return {**dict(record), 'formatted_time': ts}

        return render_template('dashboard.html',
                               anomalies=[format_time(a) for a in anomalies],
                               forecasts=[format_time(f) for f in forecasts],
                               model_info=dict(
                                   model_info) if model_info else None,
                               last_updated=datetime.now().strftime('%Y-%m-%d %H:%M:%S'))
    except sqlite3.Error as e:
        logger.error(f"Dashboard error: {str(e)}")
        return render_template('error.html'), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route('/api/anomalies')
def get_anomalies():
    try:
        conn = get_db_connection()
        anomalies = conn.execute('''
            SELECT detection_time AS timestamp, active_power AS value, 'anomaly' AS type,
                   device_id, is_anomaly, is_injected_anomaly
            FROM anomalies
            ORDER BY detection_time DESC
            LIMIT 100
        ''').fetchall()
        response = jsonify([dict(row) for row in anomalies])
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    except sqlite3.Error as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({"error": "Database query failed"}), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route('/api/forecasts')
def get_forecasts():
    try:
        conn = get_db_connection()
        forecasts = conn.execute('''
            SELECT timestamp, prediction AS value, 'forecast' AS type, active_power
            FROM forecasts
            ORDER BY timestamp DESC
            LIMIT 100
        ''').fetchall()
        formatted_forecasts = []
        for row in forecasts:
            row_dict = dict(row)
            try:
                ts = datetime.fromisoformat(
                    row_dict['timestamp'].replace('Z', '+00:00'))
                row_dict['timestamp'] = ts.isoformat()
            except:
                pass
            formatted_forecasts.append(row_dict)
        response = jsonify(formatted_forecasts)
        response.headers['Cache-Control'] = 'no-store, no-cache, must-revalidate, max-age=0'
        return response
    except sqlite3.Error as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({"error": "Database query failed"}), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route('/api/model_info')
def get_model_info():
    try:
        conn = get_db_connection()
        model_info = conn.execute('''
            SELECT model_timestamp, rmse, r2, mae, mse, created_at
            FROM model_info
            ORDER BY created_at DESC
            LIMIT 1
        ''').fetchone()
        return jsonify(dict(model_info) if model_info else {})
    except sqlite3.Error as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({"error": "Database query failed"}), 500
    finally:
        if 'conn' in locals():
            conn.close()


@app.route('/api/counts')
def get_counts():
    try:
        conn = get_db_connection()
        anomaly_count = conn.execute(
            'SELECT COUNT(*) FROM anomalies').fetchone()[0]
        forecast_count = conn.execute(
            'SELECT COUNT(*) FROM forecasts').fetchone()[0]
        conn.close()
        return jsonify({
            "anomaly_count": anomaly_count,
            "forecast_count": forecast_count
        })
    except sqlite3.Error as e:
        logger.error(f"API error: {str(e)}")
        return jsonify({"error": "Database query failed"}), 500


@app.route('/predict', methods=['GET', 'POST'])
def predict():
    # Check if we have a model available
    global model
    if model is None:
        try:
            model = load_latest_model()
        except Exception as e:
            logger.error(f"Failed to load model on demand: {str(e)}")

    if model is None:
        return render_template('predict.html', error="No model available for prediction. Please check the system logs.", prediction=None)

    if request.method == 'POST':
        try:
            # Validate input data
            input_values, validation_errors = validate_input(request.form)

            if validation_errors:
                error_message = "Please fix the following errors: " + \
                    ", ".join(validation_errors)
                return render_template('predict.html', error=error_message, prediction=None)

            # Create input dataframe with proper types
            input_data = [(
                float(input_values["Voltage"]),
                float(input_values["current"]),
                float(input_values["Sub_metering_1"]),
                float(input_values["Sub_metering_2"]),
                float(input_values["Sub_metering_3"]),
                int(input_values["hour"]),
                int(input_values["day_of_week"]),
                int(input_values["month"])
            )]

            # Create schema with proper types
            from pyspark.sql.types import StructType, StructField, DoubleType, IntegerType
            schema = StructType([
                StructField("Voltage", DoubleType(), True),
                StructField("current", DoubleType(), True),
                StructField("Sub_metering_1", DoubleType(), True),
                StructField("Sub_metering_2", DoubleType(), True),
                StructField("Sub_metering_3", DoubleType(), True),
                StructField("hour", IntegerType(), True),
                StructField("day_of_week", IntegerType(), True),
                StructField("month", IntegerType(), True)
            ])

            input_df = spark.createDataFrame(input_data, schema)

            # Make prediction based on available model type
            if pipeline_model is not None:
                # If we have a pipeline model, use it directly
                predictions = pipeline_model.transform(input_df)
                prediction_value = predictions.select(
                    "prediction").collect()[0][0]
            else:
                # Otherwise, manually do the feature engineering
                assembler = VectorAssembler(
                    inputCols=feature_cols, outputCol="features_raw", handleInvalid="skip")
                scaler = StandardScaler(
                    inputCol="features_raw", outputCol="features", withStd=True, withMean=True)

                assembled_df = assembler.transform(input_df)

                # Try to use scaling parameters from model_info if available
                if model_info and 'feature_scaling' in model_info:
                    # Use saved scaling parameters
                    scaling_params = json.loads(model_info['feature_scaling'])
                    # Apply manual scaling using saved parameters
                    # This is a simplified approach - in a real system you'd implement this properly
                    # scaled_df = apply_manual_scaling(assembled_df, scaling_params)
                    scaled_df = scaler.fit(
                        assembled_df).transform(assembled_df)
                else:
                    # Fallback to fit on current data (not ideal)
                    scaled_df = scaler.fit(
                        assembled_df).transform(assembled_df)

                predictions = model.transform(scaled_df)
                prediction_value = predictions.select(
                    "prediction").collect()[0][0]

            # Store prediction for reference
            try:
                conn = get_db_connection()
                current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                conn.execute('''
                    INSERT INTO forecasts (timestamp, active_power, prediction, created_at)
                    VALUES (?, NULL, ?, ?)
                ''', (current_time, prediction_value, current_time))
                conn.commit()
                conn.close()
            except Exception as e:
                logger.warning(f"Failed to store prediction: {str(e)}")

            return render_template('predict.html', prediction=round(prediction_value, 3), error=None)

        except Exception as e:
            logger.error(f"Prediction error: {str(e)}")
            return render_template('predict.html', error=f"Prediction failed: {str(e)}", prediction=None)

    return render_template('predict.html', prediction=None, error=None)


@app.route('/benchmarks')
def benchmarks():
    return render_template('benchmarks.html')


@app.route('/reload_model')
def reload_model():
    """Admin endpoint to force model reload"""
    global model, pipeline_model
    try:
        model = None
        pipeline_model = None
        model = load_latest_model()
        if model:
            return jsonify({"status": "success", "message": "Model reloaded successfully"})
        else:
            return jsonify({"status": "error", "message": "Failed to reload model"}), 500
    except Exception as e:
        logger.error(f"Model reload error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=False)

# Databricks notebook source
# MAGIC %md
# MAGIC # Azure Databricks Quickstart for Data Engineers
# MAGIC Welcome to the quickstart lab for data engineers on Azure Databricks! Over the course of this notebook, you will use a real-world dataset and learn how to:
# MAGIC 1. Access your enterprise data lake in Azure using Databricks
# MAGIC 2. Transform and store your data in a reliable and performant Delta Lake
# MAGIC 3. Use Update,Delete,Merge,Schema Evolution and Time Travel Capabilities, CDF (Change Data Feed) of Delta Lake
# MAGIC 
# MAGIC ## The Use Case
# MAGIC We will analyze public subscriber data from a popular Korean music streaming service called KKbox stored in Azure Blob Storage. The goal of the notebook is to answer a set of business-related questions about our business, subscribers and usage. 

# COMMAND ----------

# MAGIC %md
# MAGIC ##Initial Set-up
# MAGIC 
# MAGIC ### Enter the Blob_Container , Blob_Account and Account_Key for the Cloudlabs Environment

# COMMAND ----------

dbutils.widgets.text("ACCOUNT_KEY", "", "ACCOUNT_KEY")
dbutils.widgets.text("BLOB_CONTAINER", "", "BLOB_CONTAINER")
dbutils.widgets.text("BLOB_ACCOUNT", "", "BLOB_ACCOUNT")

# COMMAND ----------

BLOB_CONTAINER = dbutils.widgets.get("BLOB_CONTAINER")
BLOB_ACCOUNT = dbutils.widgets.get("BLOB_ACCOUNT")
ACCOUNT_KEY = dbutils.widgets.get("ACCOUNT_KEY")

# COMMAND ----------

# DBTITLE 1,Mount Blob Storage to DBFS
run = dbutils.notebook.run('./Setup Notebooks/00 - Setup Storage', 60, {"BLOB_CONTAINER" : BLOB_CONTAINER,"BLOB_ACCOUNT" : BLOB_ACCOUNT,"ACCOUNT_KEY" : ACCOUNT_KEY })

# COMMAND ----------

# DBTITLE 1,Install libraries
# MAGIC %run "../ADBQuickStartLabs/Setup Notebooks/00 - Libraries Setup"

# COMMAND ----------

# DBTITLE 1,Delete existing files
#import shutil
# pyspark.sql.types import *
# delete the old database and tables if needed
_ = spark.sql('DROP DATABASE IF EXISTS kkbox CASCADE')

# drop any old delta lake files that might have been created
dbutils.fs.rm('/mnt/adbquickstart/bronze', recurse=True)
dbutils.fs.rm('/mnt/adbquickstart/gold', recurse=True)
dbutils.fs.rm('/mnt/adbquickstart/silver', recurse=True)
dbutils.fs.rm('/mnt/adbquickstart/checkpoint', recurse=True)
# create database to house SQL tables
_ = spark.sql('CREATE DATABASE kkbox')

# COMMAND ----------

# MAGIC %md
# MAGIC <!-- #DATA ENGINEERING AND STREAMING ARCHITECTURE -->
# MAGIC <!-- <img src="https://kpistoropen.blob.core.windows.net/collateral/quickstart/etl.png" width=1500> -->
# MAGIC <img src="https://publicimg.blob.core.windows.net/images/DE and Streaming.png" width="1200">

# COMMAND ----------

# MAGIC %md
# MAGIC Once mounted, we can view and navigate the contents of our container using Databricks `%fs` file system commands.

# COMMAND ----------

# MAGIC %fs ls /mnt/adbquickstart/

# COMMAND ----------

# MAGIC %md
# MAGIC ### Explore Your Data
# MAGIC In 2018, [KKBox](https://www.kkbox.com/) - a popular music streaming service based in Taiwan - released a [dataset](https://www.kaggle.com/c/kkbox-churn-prediction-challenge/data) consisting of a little over two years of (anonymized) customer transaction and activity data with the goal of challenging the Data & AI community to predict which customers would churn in a future period.  
# MAGIC 
# MAGIC The primary data files are organized in the storage container:
# MAGIC 
# MAGIC <img src='https://brysmiwasb.blob.core.windows.net/demos/images/kkbox_filedownloads.png' width=150>
# MAGIC 
# MAGIC Read into dataframes, these files form the following data model:
# MAGIC 
# MAGIC <img src='https://brysmiwasb.blob.core.windows.net/demos/images/kkbox_schema.png' width=150>
# MAGIC 
# MAGIC Each subscriber is uniquely identified by a value in the `msno` field of the `members` table. Data in the `transactions` and `user_logs` tables provide a record of subscription management and streaming activities, respectively.  

# COMMAND ----------

# MAGIC %md
# MAGIC ##In this Demo notebook we will showcase some of the most common scenarios Data Engineers encouter while working on ingesting and data processing
# MAGIC ####1. Ingest Data in Batch Process
# MAGIC ####2. Ingest Data using Autoloader and COPY INTO command
# MAGIC ####3. Perform operations such as Update, Merge , Delete on delta tables

# COMMAND ----------

# MAGIC %md
# MAGIC ### SCENARIO 1  : INGEST DATA in BATCH PROCESS (Parquet Files)
# MAGIC ##### In this scenario we will ingest an inital load of transactional data to Delta format. We will ingest one data sets : (Transaction Dataset : Parquet Format) and convert it to Delta(bronze layer)

# COMMAND ----------

# DBTITLE 1,Prep Transactions Dataset - Parquet Files to Delta
# Define transaction dataset schema
transaction_schema = StructType([
  StructField('msno', StringType()),
  StructField('payment_method_id', IntegerType()),
  StructField('payment_plan_days', IntegerType()),
  StructField('plan_list_price', IntegerType()),
  StructField('actual_amount_paid', IntegerType()),
  StructField('is_auto_renew', IntegerType()),
  StructField('transaction_date', DateType()),
  StructField('membership_expire_date', DateType()),
  StructField('is_cancel', IntegerType())  
  ])

# Read data from parquet files
transactions = (
  spark
    .read
    .parquet(
      '/mnt/adbquickstart/transactions',
      schema=transaction_schema,
      header=True,
      dateFormat='yyyyMMdd'
      )
    )

# persist in delta lake format
( transactions
    .write
    .format('delta')
    .partitionBy('transaction_date')
    .mode('overwrite')
    .save('/mnt/adbquickstart/bronze/transactions')
  )

# create table object to make delta lake queriable
spark.sql('''
  CREATE TABLE kkbox.transactions
  USING DELTA 
  LOCATION '/mnt/adbquickstart/bronze/transactions'
  ''')

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM kkbox.transactions

# COMMAND ----------

# DBTITLE 1,Let's look at the Delta Files
# MAGIC %fs ls /mnt/adbquickstart/bronze/transactions

# COMMAND ----------

# MAGIC %md
# MAGIC ### SCENARIO 2  : INGEST DATA with Databricks AutoLoader and COPY INTO

# COMMAND ----------

# MAGIC %md
# MAGIC ####Auto Loader, COPY INTO and Incrementally Ingesting Data
# MAGIC Auto Loader and COPY INTO are two methods of ingesting data into a Delta Lake table from a folder in a Data Lake. “Yeah, so... Why is that so special?”, you may ask. The reason these features are special is that they make it possible to ingest data directly from a data lake incrementally, in an idempotent way, without needing a distributed streaming system like Kafka. This can considerably simplify the Incremental ETL process. It is also an extremely efficient way to ingest data since you are only ingesting new data and not reprocessing data that already exists. Below is an Incremental ETL architecture. We will focus on the left hand side, ingesting into tables from outside sources. 
# MAGIC 
# MAGIC You can incrementally ingest data either continuously or scheduled in a job. COPY INTO and Auto Loader cover both cases and we will show you how below.
# MAGIC 
# MAGIC <img src="https://databricks.com/wp-content/uploads/2021/07/get-start-delta-blog-img-1.png" width=1000>

# COMMAND ----------

# MAGIC %md
# MAGIC ### Overview Autoloader
# MAGIC [Auto Loader](https://docs.databricks.com/spark/latest/structured-streaming/auto-loader-gen2.html) is an ingest feature of Databricks that makes it simple to incrementally ingest only new data from Azure Data Lake. In this notebook we will use Auto Loader for a basic ingest use case but there are many features of Auto Loader, like [schema inference and evolution](https://docs.databricks.com/spark/latest/structured-streaming/auto-loader-gen2.html#schema-inference-and-evolution), that make it possible to ingest very complex and dynymically changing data

# COMMAND ----------

# DBTITLE 1,Prep Members Dataset - Ingest via Autoloader
# "cloudFiles" indicates the use of Auto Loader

dfBronze = spark.readStream.format("cloudFiles") \
  .option('cloudFiles.format', 'csv') \
  .option('header','true') \
  .schema('msno string, city int, bd int, gender string ,registered_via int , registration_init_time string') \
  .load("/mnt/adbquickstart/members/")

#.option("cloudFiles.schemaLocation", "/mnt/adbquickstart/schema/members") \

# The stream will shut itself off when it is finished based on the trigger once feature
# The checkpoint location saves the state of the ingest when it is shut off so we know where to pick up next time
dfBronze.writeStream \
  .format("delta") \
  .trigger(once=True) \
  .option("checkpointLocation", "/mnt/adbquickstart/checkpoint/members") \
  .start("/mnt/adbquickstart/bronze/members")

# COMMAND ----------

# DBTITLE 1,Cool.. Lets see if we could see the files in the member delta folder
# MAGIC %fs ls /mnt/adbquickstart/bronze/members/

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE kkbox.members
# MAGIC USING DELTA 
# MAGIC LOCATION '/mnt/adbquickstart/bronze/members'

# COMMAND ----------

# MAGIC %sql
# MAGIC select * from kkbox.members

# COMMAND ----------

# MAGIC %md
# MAGIC ### INGEST DATA with Databricks COPY INTO

# COMMAND ----------

# MAGIC %sql
# MAGIC COPY INTO delta.`/mnt/adbquickstart/bronze/user_log/`
# MAGIC     FROM '/mnt/adbquickstart/user_logs/'
# MAGIC     FILEFORMAT = CSV
# MAGIC     FORMAT_OPTIONS('header' = 'true')

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE kkbox.user_log
# MAGIC USING DELTA 
# MAGIC LOCATION '/mnt/adbquickstart/bronze/user_log'

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM kkbox.user_log

# COMMAND ----------

# DBTITLE 1,Great !!! Now lets read the data and see if everything went well.
## Read the Bronze Data
transactions_bronze = spark.read.format("delta").load('/mnt/adbquickstart/bronze/transactions/')
members_bronze = spark.read.format("delta").load('/mnt/adbquickstart/bronze/members/')
user_logs_bronze = spark.read.format("delta").load('/mnt/adbquickstart/bronze/user_log/')

# COMMAND ----------

# MAGIC %md
# MAGIC ### Scenario 3 - Delta Features
# MAGIC #### Further we are concentrating on Members dataset. We will create a Gold table (Aggregated table)

# COMMAND ----------

# MAGIC %sql
# MAGIC set spark.databricks.delta.properties.defaults.enableChangeDataFeed = true;

# COMMAND ----------

# MAGIC %sql DROP TABLE IF EXISTS kkbox.members_gold

# COMMAND ----------

# dbutils.fs.rm("/mnt/adbquickstart/gold/members/",recurse=True)

# COMMAND ----------

# DBTITLE 1,Members by Registration Year - Create a Gold table
import pyspark.sql.functions as f
members_transform = members_bronze.withColumn('years',members_bronze['registration_init_time'].substr(1, 4))

members_gold = members_transform.groupBy('years').count()

members_gold.createOrReplaceTempView("member_gold")

#Save our Gold table in Delta format and Enable CDC on the Delta Table

# members_gold.write.format('delta').mode('overwrite').save('/mnt/adbquickstart/gold/members/')
members_gold.write.format('delta').mode('overwrite').option('path', '/mnt/adbquickstart/gold/members/').saveAsTable('kkbox.members_gold')

display(members_gold)

# COMMAND ----------

# DBTITLE 1,Query Gold table using file path
# MAGIC %sql
# MAGIC SELECT * from delta.`/mnt/adbquickstart/gold/members/`

# COMMAND ----------

# DBTITLE 1,Query Gold table - 
# MAGIC %sql
# MAGIC select * from kkbox.members_gold

# COMMAND ----------

# MAGIC %md 
# MAGIC 
# MAGIC ### Delta as Unified Batch and Streaming Source and Sink
# MAGIC 
# MAGIC These cells showcase streaming and batch concurrent queries (inserts and reads)
# MAGIC * This notebook will run an `INSERT` every 10s against our `members_gold` table
# MAGIC * We will run two streaming queries concurrently against this data and update the table

# COMMAND ----------

# DBTITLE 1,Stop the notebook before the streaming cell, in case of a "run all" 
dbutils.notebook.exit("stop") 

# COMMAND ----------

# Read the insertion of data
members_gold_readStream = spark.readStream.format("delta").load('/mnt/adbquickstart/gold/members/')
members_gold_readStream.createOrReplaceTempView("members_gold_readStream")

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT years, sum(`count`) AS members
# MAGIC FROM members_gold_readStream
# MAGIC GROUP BY years
# MAGIC ORDER BY years

# COMMAND ----------

# DBTITLE 1,Insert new rows - Second Stream
import time
i = 1
while i <= 6:
  # Execute Insert statement
  insert_sql = "INSERT INTO kkbox.members_gold VALUES (2004, 450000)"
  spark.sql(insert_sql)
  print('members_gold_delta: inserted new row of data, loop: [%s]' % i)
    
  # Loop through
  i = i + 1
  time.sleep(10)

# COMMAND ----------

# MAGIC %md
# MAGIC ### Perform DML operations , Schema Evolution and Time Travel
# MAGIC #####Delta Lake supports standard DML including UPDATE, DELETE and MERGE INTO providing data engineers more controls to manage their big datasets.

# COMMAND ----------

# MAGIC %md ### DELETE Support

# COMMAND ----------

# MAGIC %sql
# MAGIC -- Running `DELETE` on the Delta Lake table to remove records from year 2009
# MAGIC DELETE FROM kkbox.members_gold WHERE years = 2009

# COMMAND ----------

# DBTITLE 1,Let's confirm the data is deleted for year 2009
# MAGIC %sql
# MAGIC SELECT * FROM kkbox.members_gold
# MAGIC ORDER BY years

# COMMAND ----------

# MAGIC %md ### UPDATE Support

# COMMAND ----------

# DBTITLE 1,Let's update the count for year 2010
# MAGIC %sql
# MAGIC UPDATE kkbox.members_gold SET `count` = 50000 WHERE years = 2010

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM kkbox.members_gold
# MAGIC ORDER BY years

# COMMAND ----------

# MAGIC %md ###MERGE INTO Support
# MAGIC 
# MAGIC #### INSERT or UPDATE with Delta Lake: 2-step process
# MAGIC 
# MAGIC With Delta Lake, inserting or updating a table is a simple 2-step process: 
# MAGIC 1. Identify rows to insert or update
# MAGIC 2. Use the `MERGE` command

# COMMAND ----------

# DBTITLE 1,Let's create a simple table to merge
items = [(2009, 50000), (2021, 250000), (2012, 35000)]
cols = ['years', 'count']
merge_table = spark.createDataFrame(items, cols)
merge_table.createOrReplaceTempView("merge_table")
display(merge_table)

# COMMAND ----------

# MAGIC %md Instead of writing separate `INSERT` and `UPDATE` statements, we can use a `MERGE` statement. 

# COMMAND ----------

# MAGIC %sql
# MAGIC MERGE INTO kkbox.members_gold as d
# MAGIC USING merge_table as m
# MAGIC on d.years = m.years
# MAGIC WHEN MATCHED THEN 
# MAGIC   UPDATE SET *
# MAGIC WHEN NOT MATCHED 
# MAGIC   THEN INSERT *

# COMMAND ----------

# DBTITLE 1,Perfect!! Let's check to make sure it worked
# MAGIC %sql
# MAGIC SELECT * FROM kkbox.members_gold
# MAGIC ORDER BY years

# COMMAND ----------

# MAGIC %md
# MAGIC ### Schema Evolution
# MAGIC With the `mergeSchema` option, you can evolve your Delta Lake table schema

# COMMAND ----------

# DBTITLE 1,Generate a new "usage" column in a dummy table
member_dummy = sql("SELECT years, count, CAST(rand(10) * 10 * count AS double) AS usage FROM kkbox.members_gold")
display(member_dummy)

# COMMAND ----------

# DBTITLE 1,Merge it to the delta table
# MAGIC %python
# MAGIC # Add the mergeSchema option
# MAGIC member_dummy.write.option("mergeSchema","true").format("delta").mode("append").save('/mnt/adbquickstart/gold/members/')

# COMMAND ----------

# MAGIC %sql
# MAGIC select * from kkbox.members_gold

# COMMAND ----------

# MAGIC %md ###Let's Travel back in Time!
# MAGIC Databricks Delta’s time travel capabilities simplify building data pipelines for the following use cases. 
# MAGIC 
# MAGIC * Audit Data Changes
# MAGIC * Reproduce experiments & reports
# MAGIC * Rollbacks
# MAGIC 
# MAGIC As you write into a Delta table or directory, every operation is automatically versioned.
# MAGIC 
# MAGIC You can query by:
# MAGIC 1. Using a timestamp
# MAGIC 1. Using a version number
# MAGIC 
# MAGIC using Python, Scala, and/or Scala syntax; for these examples we will use the SQL syntax.  
# MAGIC 
# MAGIC For more information, refer to [Introducing Delta Time Travel for Large Scale Data Lakes](https://databricks.com/blog/2019/02/04/introducing-delta-time-travel-for-large-scale-data-lakes.html)

# COMMAND ----------

# DBTITLE 1,Review Delta Lake Table History
# MAGIC %sql
# MAGIC DESCRIBE HISTORY kkbox.members_gold

# COMMAND ----------

# MAGIC %md ####  Time Travel via Version Number
# MAGIC Below are SQL syntax examples of Delta Time Travel by using a Version Number

# COMMAND ----------

# DBTITLE 1,Let's look at the version 0 - When the table was created
# MAGIC %sql
# MAGIC SELECT * FROM kkbox.members_gold VERSION AS OF 0
# MAGIC order by years

# COMMAND ----------

# MAGIC %md
# MAGIC ### I can even roll back my table

# COMMAND ----------

# MAGIC %sql
# MAGIC RESTORE TABLE kkbox.members_gold TO VERSION AS OF 9

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM kkbox.members_gold

# COMMAND ----------

# MAGIC %md
# MAGIC ### Or even back shallow or deep clones of a table (backups or testing tables)

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE OR REPLACE TABLE delta.`/mnt/adbquickstart/gold/members_back/`
# MAGIC DEEP CLONE delta.`/mnt/adbquickstart/gold/members/`

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM delta.`/mnt/adbquickstart/gold/members_back/`

# COMMAND ----------

# DBTITLE 0,Simplify Your Medallion Architecture with Delta Lake’s CDF Featurentitled
# MAGIC %md 
# MAGIC ### Simplify Your Medallion Architecture with Delta Lake’s CDF Feature
# MAGIC 
# MAGIC ### Overview
# MAGIC The medallion architecture takes raw data landed from source systems and refines the data through bronze, silver and gold tables. It is an architecture that the MERGE operation and log versioning in Delta Lake make possible. Change data capture (CDC) is a use case that we see many customers implement in Databricks. We are happy to announce an exciting new Change data feed (CDF) feature in Delta Lake that makes this architecture even simpler to implement!
# MAGIC 
# MAGIC The following example ingests financial data. Estimated Earnings Per Share (EPS) is financial data from analysts predicting what a company’s quarterly earnings per share will be. The raw data can come from many different sources and from multiple analysts for multiple stocks. The data is simply inserted into the bronze table, it will  change in the silver and then aggregate values need to be recomputed in the gold table based on the changed data in the silver. 
# MAGIC 
# MAGIC While these transformations can get complex, thankfully now the row based CDF feature can be simple and efficient but how do you use it? Let’s dig in!
# MAGIC 
# MAGIC <img src="https://databricks.com/wp-content/uploads/2021/05/cdf-blog-img-1-rev.png" width=600>

# COMMAND ----------

# MAGIC %fs ls '/mnt/adbquickstart/gold/members/'

# COMMAND ----------

# MAGIC %sql
# MAGIC SELECT * FROM table_changes('kkbox.members_gold', 1)

# COMMAND ----------

# MAGIC %md
# MAGIC ### IDENTITY COLUMN
# MAGIC Delta Lake now supports identity columns. When you write to a Delta table that defines an identity column, and you do not provide values for that column, Delta now automatically assigns a unique and statistically increasing or decreasing value.

# COMMAND ----------

# MAGIC %sql
# MAGIC CREATE TABLE kkbox.members_new
# MAGIC ( ID BIGINT GENERATED ALWAYS AS IDENTITY,
# MAGIC   years STRING, 
# MAGIC   count LONG
# MAGIC )
# MAGIC USING delta 

# COMMAND ----------

# MAGIC %sql
# MAGIC INSERT INTO kkbox.members_new (years, count) TABLE member_gold

# COMMAND ----------

# DBTITLE 1,So our original table did not had a identity column
# MAGIC %sql 
# MAGIC select * from member_gold;

# COMMAND ----------

# DBTITLE 1,Let's look at the identity column
# MAGIC %sql 
# MAGIC select * from kkbox.members_new;

# COMMAND ----------

# DBTITLE 1,Finally !!! Lets End this with with some performance enhancement feature 
# MAGIC %md #####  OPTIMIZE (Delta Lake on Databricks)
# MAGIC Optimizes the layout of Delta Lake data. Optionally optimize a subset of data or colocate data by column. If you do not specify colocation, bin-packing optimization is performed.

# COMMAND ----------

# MAGIC %sql OPTIMIZE kkbox.user_log ZORDER BY (date)

# COMMAND ----------

# MAGIC %md
# MAGIC ### ANALYZE TABLE
# MAGIC The ANALYZE TABLE statement collects statistics about one specific table or all the tables in one specified database, that are to be used by the query optimizer to find a better query execution plan

# COMMAND ----------

# MAGIC %sql
# MAGIC ANALYZE TABLE kkbox.user_log COMPUTE STATISTICS;

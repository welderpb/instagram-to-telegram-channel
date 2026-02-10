# Use an official Python runtime as a parent image
# 'slim' variants are smaller and safer
FROM python:3.10-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file into the container at /app
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
# --no-cache-dir reduces the image size
RUN pip install --no-cache-dir -r requirements.txt

# Copy the current directory contents into the container at /app
COPY . .

# Run the bot when the container launches
# -u ensures python output is sent straight to terminal (unbuffered)
CMD ["python", "-u", "insta_bot.py"]

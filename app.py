from flask import Flask, jsonify, request, url_for
from flask import Flask, render_template, request, jsonify, Response
from flask_pymongo import PyMongo
import csv
from io import StringIO
from bson import ObjectId
import json

app = Flask(__name__)
app.config.from_object('config.Config')
mongo = PyMongo(app)

# Custom JSON Encoder
class JSONEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, ObjectId):
            return str(obj)
        return super().default(obj)

app.json_encoder = JSONEncoder

# Simulated user database
users = {'jeny': {'password': 'jeny', 'userType': 'admin'}}

# Helper functions
def get_absentees(date, section, subject, attendance_records):
    absentees = []
    for record in attendance_records:
        if record['status'] != 'present':
            absentees.append(record['student_id'])

    # Store absentees in MongoDB
    absentees_collection = mongo.db[f'attendance_{section}_absentees']
    absentees_collection.insert_one({
        "date": date,
        "subject": subject,
        "absentees": absentees
    })
    return absentees

def get_bunkers(date, section, subject, current_period, attendance_records):
    bunkers = []
    attendance_collection = mongo.db[f'attendance_{section}']

    for record in attendance_records:
        student_id = record['student_id']
        status = record['status']

        # Check all previous periods for the current date
        previous_records = attendance_collection.find_one(
            {"student_id": student_id},
            {f"{date}": 1, "_id": 0}
        )

        if previous_records and status == 'absent':
            for period, data in previous_records[date].items():
                if data['status'] == 1:  # Check if previously present
                    # Extract period and subject information from the current record
                    period_num = int(period.split('period')[1])
                    bunkers.append({
                        "student_id": student_id,
                        "subject": subject,
                        "date": date
                    })
                    break

    # Store bunkers in MongoDB
    bunkers_collection = mongo.db[f'attendance_{section}_bunkers']
    if bunkers:
        bunkers_collection.insert_many(bunkers)

    return bunkers

# Routes
@app.route('/')
def home():
    return render_template('index.html')

@app.route('/favicon.ico')
def favicon():
    return "", 204

@app.route('/submit', methods=['POST'])
def submit_attendance():
    data = request.json
    date = data['date']
    period = data['period']  # This is the user-selected period
    subject = data['subject']
    section = data['section']
    attendance_records = data['attendance_records']

    # Dynamically select the collection based on section
    attendance_collection = mongo.db[f'attendance_{section}']

    for record in attendance_records:
        student_id = record['student_id']
        status = 1 if record['status'] == 'present' else 0

        # Construct the attendance record for the current period
        attendance_record = {"subject": subject, "status": status}

        # Update the attendance collection
        attendance_collection.update_one(
            {"student_id": student_id},
            {
                "$set": {
                    f"{date}.period{period}": attendance_record
                }
            },
            upsert=True
        )
    
    # Get the list of absentees
    absentees = get_absentees(date, section, subject, attendance_records)
    absentees = [str(absentee) for absentee in absentees]  # Ensure all IDs are strings

    # Pass the period to the get_bunkers function
    bunkers = get_bunkers(date, section, subject, period, attendance_records)
    bunkers = [dict(record, **{'student_id': str(record['student_id'])}) for record in bunkers]  # Ensure IDs are strings

    response_data = {
        "message": "Attendance marked successfully",
        "absentees": absentees,
        "bunkers": bunkers
    }

    # Use JSONEncoder directly in jsonify
    return app.response_class(
        response=json.dumps(response_data, cls=JSONEncoder),
        mimetype='application/json',
        status=201
    )


@app.route('/view_statistics', methods=['POST'])
def view_statistics():
    data = request.get_json()
    section = data['section']
    start_date = data['start_date']
    end_date = data['end_date']
    
    # Fetch attendance statistics from MongoDB
    pipeline = [
        {'$match': {'section': section, 'date': {'$gte': start_date, '$lte': end_date}}},
        {'$group': {
            '_id': '$student_id',
            'total': {'$sum': 1},
            'present': {'$sum': {'$cond': [{'$eq': ['$status', 1]}, 1, 0]}}
        }},
        {'$project': {
            'attendance_percentage': {'$multiply': [{'$divide': ['$present', '$total']}, 100]}
        }}
    ]
    stats = list(mongo.db[f'attendance_{section}'].aggregate(pipeline))
    return jsonify(stats)


@app.route('/view_users', methods=['GET'])
def view_users():
    users = list(mongo.db.users.find())
    users = [dict(user, **{'_id': str(user['_id'])}) for user in users]
    return app.response_class(
        response=json.dumps(users, cls=JSONEncoder),
        mimetype='application/json'
    )

@app.route('/edit_user', methods=['POST'])
def edit_user():
    data = request.get_json()
    user_id = data['user_id']
    update_fields = {
        'username': data['username'],
        'password': data['password'],
        'mobile': data['mobile']
    }
    mongo.db.users.update_one({'_id': ObjectId(user_id)}, {'$set': update_fields})
    return jsonify({'success': True})

@app.route('/delete_user', methods=['POST'])
def delete_user():
    data = request.get_json()
    user_id = data['user_id']
    mongo.db.users.delete_one({'_id': ObjectId(user_id)})
    return jsonify({'success': True})

@app.route('/register', methods=['POST'])
def register():
    data = request.get_json()
    username = data['username']
    password = data['password']
    mobile = data['mobile']
    if mongo.db.users.find_one({'username': username}):
        return jsonify({'success': False, 'message': 'Username already exists'})
    mongo.db.users.insert_one({'username': username, 'password': password, 'mobile': mobile})
    return jsonify({'success': True})

@app.route('/login', methods=['POST'])
def login():
    data = request.get_json()
    username = data['username']
    password = data['password']

    # Special case for admin user "jeny"
    if username == 'jeny' and password == 'jeny':
        return jsonify({'success': True, 'userType': 'admin', 'redirect': url_for('admin_dashboard')})

    user = mongo.db.users.find_one({'username': username, 'password': password})
    if user:
        user_type = user.get('userType', 'teacher')
        if user_type == 'admin':
            return jsonify({'success': True, 'userType': 'admin', 'redirect': url_for('admin_dashboard')})
        else:
            # Redirect teachers to teachers.html
            return jsonify({'success': True, 'userType': 'teacher', 'redirect': url_for('index1')})
    
    return jsonify({'success': False})

# Define a route for the index page
@app.route('/index1.html')
def index1():
    return render_template('index1.html')

@app.route('/admin_dashboard.html')
def admin_dashboard():
    return render_template('admin_dashboard.html')

@app.route('/teacher_dashboard.html')
def teacher_dashboard():
    return render_template('teacher_dashboard.html')

@app.route('/get_students', methods=['GET'])
def get_students():
    section = request.args.get('section')
    students = list(mongo.db.students.find({"year": 2, "section": section}, {"_id": 0}))
    return jsonify(students)

@app.route('/calculate_attendance', methods=['GET'])
def calculate_attendance():
    section = request.args.get('section')
    subject = request.args.get('subject')

    # Dynamically select the collection based on section
    attendance_collection = mongo.db[f'attendance_{section}']

    # Retrieve all students' attendance records
    students = attendance_collection.find({}, {"student_id": 1, "_id": 0})

    attendance_percentage = []

    for student in students:
        student_id = student['student_id']
        total_classes_attended = 0
        total_classes_conducted = 0

        # Retrieve the attendance records for the student
        student_records = attendance_collection.find({"student_id": student_id})
        
        for record in student_records:
            for date, periods in record.items():
                if date != 'student_id':
                    # Debugging line to inspect periods data
                    print(f"Date: {date}, Periods: {periods}")
                    
                    # Ensure periods is a dictionary
                    if isinstance(periods, dict):
                        for period, data in periods.items():
                            # Ensure data is a dictionary and has 'status'
                            if isinstance(data, dict) and 'status' in data:
                                total_classes_conducted += 1
                                if data['status'] == 1:  # status 1 for present
                                    total_classes_attended += 1
                            else:
                                print(f"Unexpected data format in periods: {data}")
                    else:
                        print(f"Unexpected periods format: {periods}")

        # Calculate attendance percentage
        attendance_percentage_subject = (total_classes_attended / total_classes_conducted * 100) if total_classes_conducted else 0

        attendance_percentage.append({
            "student_id": str(student_id),
            "attendance_percentage": attendance_percentage_subject
        })

    return app.response_class(
        response=json.dumps(attendance_percentage, cls=JSONEncoder),
        mimetype='application/json'
    )

def calculate_overall_attendance(section, start_date, end_date):
    attendance_collection = mongo.db[f'attendance_{section}']
    students = attendance_collection.find()
    overall_attendance = []

    for student in students:
        total_periods = 0
        attended_periods = 0
        student_id = student.get('student_id')

        for date in student:
            if date.startswith('202'):  # Check if the key is a date
                if start_date <= date <= end_date:
                    for period, details in student[date].items():
                        total_periods += 1
                        if details['status'] == 1:
                            attended_periods += 1

        if total_periods > 0:
            attendance_percentage = (attended_periods / total_periods) * 100
        else:
            attendance_percentage = 0

        overall_attendance.append({
            'student_id': student_id,
            'total_periods': total_periods,
            'attended_periods': attended_periods,
            'attendance_percentage': attendance_percentage
        })

    average_percentage = sum(student['attendance_percentage'] for student in overall_attendance) / len(overall_attendance) if overall_attendance else 0
    return overall_attendance, average_percentage

@app.route('/calculate_overall_attendance', methods=['GET'])
def calculate_overall_attendance_route():
    section = request.args.get('section')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    overall_attendance, average_percentage = calculate_overall_attendance(section, start_date, end_date)

    return jsonify({"overall_attendance": overall_attendance, "average_percentage": average_percentage})

@app.route('/download_attendance_csv', methods=['GET'])
def download_attendance_csv():
    section = request.args.get('section')
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')

    overall_attendance, average_percentage = calculate_overall_attendance(section, start_date, end_date)

    # Create CSV file
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Student ID", "Total Periods", "Attended Periods", "Attendance Percentage"])

    for record in overall_attendance:
        writer.writerow([record['student_id'], record['total_periods'], record['attended_periods'], record['attendance_percentage']])

    writer.writerow([])
    writer.writerow(["Average Attendance Percentage", average_percentage])

    output = si.getvalue()
    response = Response(output, mimetype="text/csv")
    response.headers["Content-Disposition"] = "attachment; filename=overall_attendance.csv"
    return response

# New endpoint to download subject-wise attendance CSV
@app.route('/download_subject_attendance_csv', methods=['GET'])
def download_subject_attendance_csv():
    section = request.args.get('section')
    subject = request.args.get('subject')

    # Fetch attendance data for the given subject
    attendance_collection = mongo.db[f'attendance_{section}']
    students = attendance_collection.find()

    subject_attendance = []

    for student in students:
        student_id = student.get('student_id')
        total_periods = 0
        attended_periods = 0

        for date, date_attendance in student.items():
            if date.startswith('202'):  # Check if the key is a date
                for period, period_attendance in date_attendance.items():
                    if period_attendance.get('subject') == subject:
                        total_periods += 1
                        if period_attendance['status'] == 1:
                            attended_periods += 1

        attendance_percentage = (attended_periods / total_periods) * 100 if total_periods > 0 else 0
        subject_attendance.append({
            'student_id': student_id,
            'total_periods': total_periods,
            'attended_periods': attended_periods,
            'attendance_percentage': attendance_percentage
        })

    # Create CSV file
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow([f"Attendance Report for {subject} ({section})"])
    writer.writerow([])
    writer.writerow(["Student ID", "Total Periods", "Attended Periods", "Attendance Percentage"])

    for record in subject_attendance:
        writer.writerow([record['student_id'], record['total_periods'], record['attended_periods'], record['attendance_percentage']])

    output = si.getvalue()
    response = Response(output, mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=subject_attendance_{subject}_{section}.csv"
    return response


# New endpoint to get absentees info
@app.route('/get_absentees_info', methods=['GET'])
def get_absentees_info():
    date = request.args.get('date')
    section = request.args.get('section')

    absentees_collection = mongo.db[f'attendance_{section}_absentees']
    absentees_data = list(absentees_collection.find({"date": date}))

    subjects_absentees = {}

    for record in absentees_data:
        subject = record['subject']
        absentees = record['absentees']
        if subject not in subjects_absentees:
            subjects_absentees[subject] = []
        subjects_absentees[subject].extend(absentees)

    if not subjects_absentees:
        return jsonify({"message": "No absentees found"}), 404

    return jsonify(subjects_absentees)

# Fetch bunkers for a specific date and section
@app.route('/get_bunkers_info', methods=['GET'])
def get_bunkers_info():
    date = request.args.get('date')
    section = request.args.get('section')

    bunkers_collection = mongo.db[f'attendance_{section}_bunkers']
    bunkers_data = list(bunkers_collection.find({"date": date}))

    subjects_bunkers = {}

    for record in bunkers_data:
        subject = record['subject']
        student_id = record['student_id']
        if subject not in subjects_bunkers:
            subjects_bunkers[subject] = []
        subjects_bunkers[subject].append(student_id)

    if not subjects_bunkers:
        return jsonify({"message": "No bunkers found"}), 404

    return jsonify(subjects_bunkers)

# Download absentees CSV
@app.route('/download_absentees_csv', methods=['GET'])
def download_absentees_csv():
    date = request.args.get('date')
    section = request.args.get('section')

    response_data = get_absentees_info()
    absentees_data = response_data.json if response_data.status_code == 200 else {}

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date:", date])
    writer.writerow(["Subject", "Roll Numbers"])

    for subject, absentees in absentees_data.items():
        writer.writerow([subject, ", ".join(absentees)])

    output = si.getvalue()
    response = Response(output, mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=absentees_{section}_{date}.csv"
    return response

# Download bunkers CSV
@app.route('/download_bunkers_csv', methods=['GET'])
def download_bunkers_csv():
    date = request.args.get('date')
    section = request.args.get('section')

    response_data = get_bunkers_info()
    bunkers_data = response_data.json if response_data.status_code == 200 else {}

    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Date:", date])
    writer.writerow(["Subject", "Roll Numbers"])

    for subject, bunkers in bunkers_data.items():
        writer.writerow([subject, ", ".join(bunkers)])

    output = si.getvalue()
    response = Response(output, mimetype="text/csv")
    response.headers["Content-Disposition"] = f"attachment; filename=bunkers_{section}_{date}.csv"
    return response

def calculate_overall_attendance_charts(section, start_date, end_date):
    attendance_collection = mongo.db[f'attendance_{section}']
    students = attendance_collection.find()
    overall_attendance = []
    total_attendance_percentage = 0
    count = 0

    for student in students:
        total_periods = 0
        attended_periods = 0
        student_id = student.get('student_id')

        for date in student:
            if date.startswith('202'):  # Check if the key is a date
                if start_date <= date <= end_date:
                    for period, details in student[date].items():
                        total_periods += 1
                        if details['status'] == 1:
                            attended_periods += 1

        if total_periods > 0:
            attendance_percentage = (attended_periods / total_periods) * 100
        else:
            attendance_percentage = 0

        overall_attendance.append({
            'student_id': student_id,
            'attendance_percentage': attendance_percentage
        })

        total_attendance_percentage += attendance_percentage
        count += 1

    average_percentage = total_attendance_percentage / count if count > 0 else 0
    return overall_attendance, average_percentage

@app.route('/chart-data/<section>', methods=['GET'])
def chart_data(section):
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    
    attendance_data, avg_percentage = calculate_overall_attendance_charts(section, start_date, end_date)
    return jsonify({
        'attendance_data': attendance_data,
        'average_percentage': avg_percentage
    })


if __name__ == '__main__':
    app.run(debug=True)

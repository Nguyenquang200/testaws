from flask import (
    Flask, render_template, request, redirect, url_for, flash, send_file, session, send_from_directory, current_app
)
from flask_login import LoginManager, login_user, login_required, logout_user, UserMixin, current_user
from flask_socketio import SocketIO, emit
from werkzeug.utils import secure_filename

from datetime import datetime, timedelta, timezone
import json
import os
import threading
import schedule
import time
import smtplib
from email.mime.text import MIMEText

app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(minutes=5)
socketio = SocketIO(app)

login_manager = LoginManager(app)
login_manager.login_view = 'login'

UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'uploads')
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# For simplicity, using a hardcoded username and password
ADMIN_USERNAME = 'admin'
ADMIN_PASSWORD = 'admin'

class User(UserMixin):
    pass

tasks = []  # Define tasks globally

@login_manager.user_loader
def user_loader(username):
    if username == ADMIN_USERNAME:
        user = User()
        user.id = username
        return user

@app.before_request
def before_request():
    if current_user.is_authenticated:
        last_active = session.get('last_active', datetime.utcnow().replace(tzinfo=timezone.utc))
        if datetime.utcnow().replace(tzinfo=timezone.utc) - last_active > app.permanent_session_lifetime:
            logout_user()
            flash('Hết phiên đăng nhập. Vui lòng đăng nhập lại.', 'warning')
            return redirect(url_for('login'))

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if username == ADMIN_USERNAME and password == ADMIN_PASSWORD:
            user = User()
            user.id = username
            login_user(user)
            flash('Đăng nhập thành công!', 'success')
            session['last_active'] = datetime.utcnow().replace(tzinfo=timezone.utc)  # Lưu thời điểm đăng nhập cuối cùng vào session
            return redirect(url_for('index'))
        else:
            flash('Đăng nhập không thành công. Vui lòng kiểm tra tên đăng nhập và mật khẩu.', 'danger')

    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('Đăng xuất thành công!', 'success')
    return redirect(url_for('login'))  # Chuyển hướng đến trang đăng nhập sau khi đăng xuất

@app.route('/view_attachment/<int:task_index>')
@login_required
def view_attachment(task_index):
    try:
        task = tasks[task_index]
        attachment_path = task['attachment']

        if attachment_path:
            attachment_filename = os.path.basename(attachment_path)
            return send_file(attachment_path, as_attachment=True)

        flash('Không tìm thấy tệp đính kèm.', 'warning')
        return redirect(url_for('index'))

    except IndexError:
        flash('Vui lòng chọn công việc để xem tệp đính kèm.', 'warning')
        return redirect(url_for('index'))

@app.route('/')
@login_required
def index():
    return render_template('index.html', tasks=tasks)

@app.route('/add_task_page')
@login_required
def add_task_page():
    return render_template('add_task.html')

@app.route('/add_task', methods=['POST'])
@login_required
def add_task():
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])

    task = {
        'title': request.form['task_title'],
        'content': request.form['task_content'],
        'priority': request.form['priority'],
        'due_date': None,
        'category': request.form['category'],
        'completed': False,
        'attachment': None,
        'progress': 0
    }

    if 'due_date' in request.form and request.form['due_date']:
        try:
            task['due_date'] = datetime.strptime(request.form['due_date'], '%Y-%m-%dT%H:%M')
        except ValueError:
            flash('Ngày giờ không hợp lệ. Vui lòng nhập lại.', 'danger')
            return redirect(url_for('add_task'))

    attachment = request.files.get('attachment')

    if attachment and attachment.filename:
        filename = secure_filename(attachment.filename)
        attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
        attachment.save(attachment_path)
        task['attachment'] = attachment_path

    if task['title'] and task['content']:
        tasks.append(task)
        save_tasks_to_file()
        flash('Công việc được thêm thành công!', 'success')
    else:
        flash('Công việc không thể để trống!', 'warning')

    return redirect(url_for('index'))

def save_tasks_to_file(exclude_completed=False):
    file_path = os.path.join(os.path.dirname(__file__), 'tasks.json')

    if exclude_completed:
        incomplete_tasks = [task for task in tasks if not task['completed']]
    else:
        incomplete_tasks = tasks

    simplified_tasks = [{'title': task['title'], 'due_date': task['due_date']} for task in incomplete_tasks]

    with open(file_path, 'w') as json_file:
        json.dump(simplified_tasks, json_file, default=str)

@app.route('/delete_task/<int:task_index>', methods=['POST'])
@login_required
def delete_task(task_index):
    try:
        if request.method == 'POST':
            del tasks[task_index]
            save_tasks_to_file()
            flash('Công việc đã được xóa thành công!', 'success')
            return redirect(url_for('index'))
        else:
            return redirect(url_for('confirm_delete', task_index=task_index))
    except IndexError:
        flash('Vui lòng chọn công việc để xóa.', 'warning')
        return redirect(url_for('index'))

@app.route('/complete_task/<int:task_index>')
@login_required
def complete_task(task_index):
    try:
        tasks[task_index]['completed'] = True
        tasks[task_index]['progress'] = 100

        # Lấy thông tin về công việc cần xóa từ JSON
        completed_task = tasks[task_index]

        # Xóa thông tin về tệp đính kèm trong trường hợp có
        if 'attachment' in completed_task and completed_task['attachment']:
            attachment_path = completed_task['attachment']
            os.remove(attachment_path)

        # Lưu lại danh sách công việc vào tệp JSON (không bao gồm công việc đã hoàn thành)
        save_tasks_to_file(exclude_completed=True)

        flash('Công việc đã được đánh dấu hoàn thành và đã bị xóa khỏi JSON!', 'success')
    except IndexError:
        flash('Vui lòng chọn công việc để đánh dấu hoàn thành.', 'warning')

    return redirect(url_for('index'))

@app.route('/edit_task/<int:task_index>', methods=['GET', 'POST'])
@login_required
def edit_task(task_index):
    try:
        task = tasks[task_index]

        if request.method == 'POST':
            updated_task = {
                'title': request.form['task_title'],
                'content': request.form['task_content'],
                'priority': request.form['priority'],
                'due_date': datetime.strptime(request.form['due_date'], '%Y-%m-%dT%H:%M'),
                'category': request.form['category'],
                'completed': task['completed'],
                'attachment': task['attachment'],
                'progress': int(request.form['completion'])
            }

            new_attachment = request.files.get('attachment')
            if new_attachment and new_attachment.filename:
                filename = secure_filename(new_attachment.filename)
                attachment_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                new_attachment.save(attachment_path)
                updated_task['attachment'] = attachment_path

            tasks[task_index] = updated_task
            save_tasks_to_file()
            flash('Công việc được cập nhật thành công!', 'success')

            return redirect(url_for('index'))

        return render_template('edit_task.html', task=task, task_index=task_index)
    except IndexError:
        flash('Vui lòng chọn công việc để chỉnh sửa.', 'warning')
        return redirect(url_for('index'))

@app.route('/sort_tasks/<string:sort_criteria>')
@login_required
def sort_tasks(sort_criteria):
    if sort_criteria == 'due_date':
        tasks.sort(key=lambda x: x['due_date'])
    elif sort_criteria == 'priority':
        tasks.sort(key=lambda x: x['priority'])
    elif sort_criteria == 'completed':
        tasks.sort(key=lambda x: x['completed'])
    flash(f'Danh sách công việc đã được sắp xếp theo {sort_criteria}.', 'info')
    return redirect(url_for('index'))

@app.route('/filter_tasks/<string:category>')
@login_required
def filter_tasks(category):
    filtered_tasks = [task for task in tasks if task['category'] == category]
    return render_template('index.html', tasks=filtered_tasks, category=category)

@app.route('/confirm_delete/<int:task_index>')
@login_required
def confirm_delete(task_index):
    try:
        task = tasks[task_index]
        return render_template('confirm_delete.html', task=task, task_index=task_index)
    except IndexError:
        flash('Vui lòng chọn công việc để xác nhận xóa.', 'warning')
        return redirect(url_for('index'))

@app.route('/stats')
@login_required
def stats():
    incomplete_tasks = sum(not task['completed'] for task in tasks)
    completed_tasks = sum(task['completed'] for task in tasks)

    return render_template('stats.html', incomplete_tasks=incomplete_tasks, completed_tasks=completed_tasks)

@socketio.on('connect')
def handle_connect():
    emit_notifications()

def emit_notifications():
    now = datetime.now()
    for task in tasks:
        if task['due_date']:
            diff = task['due_date'] - now
            if diff < timedelta(hours=1):
                msg = f"{task['title']} sắp hết hạn!"
                emit('notification', {'data': msg})
                send_email_notification(task['title'], 'Sắp hết hạn!')
            elif diff < timedelta(hours=0):
                msg = f"{task['title']} đã quá hạn!"
                emit('notification', {'data': msg})
                send_email_notification(task['title'], 'Quá hạn!')

@socketio.on('completion_update')
def handle_completion_update(data):
    task_index = data['task_index']
    completion = int(data['completion'])
    tasks[task_index]['completed'] = completion
    tasks[task_index]['progress'] = 100 if completion == 1 else 0
    emit_notifications()

def send_email_notification(task_title, status):
    sender_email = 'nguyenvanhoitgm@gmail.com'
    sender_password = 'aqxnbrtdwticckao'
    receiver_email = 'vanhoivuxa02@gmail.com'

    subject = f"Công việc '{task_title}' {status}"
    body = f"Công việc '{task_title}' {status}."

    message = MIMEText(body)
    message['Subject'] = subject
    message['From'] = sender_email
    message['To'] = receiver_email

    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(sender_email, sender_password)
        server.sendmail(sender_email, receiver_email, message.as_string())

def check_deadlines():
    now = datetime.utcnow().replace(tzinfo=timezone.utc)
    for task in tasks:
        if task['due_date']:
            diff = task['due_date'] - now
            if timedelta(minutes=0) < diff <= timedelta(minutes=10):
                msg = f"Công việc '{task['title']}' sắp hết hạn!"
                emit('notification', {'data': msg})
                send_email_notification(task['title'], 'Sắp hết hạn!')
            elif diff < timedelta(minutes=0):
                msg = f"Công việc '{task['title']}' đã quá hạn!"
                emit('notification', {'data': msg})
                send_email_notification(task['title'], 'Quá hạn!')

schedule.every(1).minutes.do(check_deadlines)

def schedule_run():
    while True:
        schedule.run_pending()
        time.sleep(1)

threading.Thread(target=schedule_run, daemon=True).start()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)

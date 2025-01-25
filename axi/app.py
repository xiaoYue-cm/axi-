from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, jsonify
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
import os
import json
import logging
from werkzeug.utils import secure_filename
import fnmatch

# 设置日志
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

app = Flask(__name__)

try:
    # 确保实例文件夹存在
    instance_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'instance')
    if not os.path.exists(instance_path):
        os.makedirs(instance_path)
        logger.info(f"Created instance directory at {instance_path}")

    # 配置数据库路径
    db_path = os.path.join(instance_path, 'site.db')
    app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
    app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
    app.config['SECRET_KEY'] = 'your_secret_key_here'

    db = SQLAlchemy(app)
    logger.info("Database initialized successfully")

    class Post(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        title = db.Column(db.String(100), nullable=False)
        content = db.Column(db.Text, nullable=False)
        date_posted = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    class SmtpConfig(db.Model):
        id = db.Column(db.Integer, primary_key=True)
        sender_name = db.Column(db.String(12), nullable=False)
        smtp_user = db.Column(db.String(30), nullable=False)
        smtp_password = db.Column(db.String(20), nullable=False)
        daily_limit = db.Column(db.Integer, default=10)

    # 添加上传文件配置
    UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'uploads')
    DECODED_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
    ALLOWED_EXTENSIONS = {'lua', 'alp'}

    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
    app.config['DECODED_FOLDER'] = DECODED_FOLDER

    # 确保上传和解码目录存在
    for folder in [UPLOAD_FOLDER, DECODED_FOLDER]:
        if not os.path.exists(folder):
            os.makedirs(folder)

    def allowed_file(filename):
        return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

    @app.route('/')
    def home():
        try:
            posts = Post.query.order_by(Post.date_posted.desc()).all()
            return render_template('home.html', posts=posts)
        except Exception as e:
            logger.error(f"Error in home route: {str(e)}")
            return f"An error occurred: {str(e)}", 500

    @app.route('/post/new', methods=['GET', 'POST'])
    def new_post():
        try:
            if request.method == 'POST':
                post = Post(
                    title=request.form['title'],
                    content=request.form['content']
                )
                db.session.add(post)
                db.session.commit()
                flash('文章发布成功！', 'success')
                return redirect(url_for('home'))
            return render_template('create_post.html')
        except Exception as e:
            logger.error(f"Error in new_post route: {str(e)}")
            db.session.rollback()
            flash('发布失败，请重试', 'error')
            return render_template('create_post.html')

    @app.route('/smtp-config', methods=['GET', 'POST'])
    def smtp_config():
        try:
            logger.debug("Accessing SMTP config page")
            config = SmtpConfig.query.first()
            logger.debug(f"Current config: {config}")
            if request.method == 'POST':
                try:
                    if not config:
                        config = SmtpConfig()
                    
                    config.sender_name = request.form['sender_name']
                    config.smtp_user = request.form['smtp_user']
                    config.smtp_password = request.form['smtp_password']
                    config.daily_limit = int(request.form['daily_limit'])
                    
                    db.session.add(config)
                    db.session.commit()
                    flash('配置已保存', 'success')
                except Exception as e:
                    logger.error(f"Error saving SMTP config: {str(e)}")
                    db.session.rollback()
                    flash('保存失败，请检查输入是否正确', 'error')
                
                return redirect(url_for('smtp_config'))
                
            logger.debug("Rendering smtp_config.html template")
            return render_template('smtp_config.html', config=config)
        except Exception as e:
            logger.error(f"Error in smtp_config route: {str(e)}")
            return f"An error occurred: {str(e)}", 500

    @app.route('/upload', methods=['GET', 'POST'])
    def upload_file():
        try:
            # 获取已解码文件列表
            decoded_files = []
            if os.path.exists(app.config['DECODED_FOLDER']):
                decoded_files = [f for f in os.listdir(app.config['DECODED_FOLDER']) 
                               if os.path.isfile(os.path.join(app.config['DECODED_FOLDER'], f))]
            
            if request.method == 'POST':
                if 'file' not in request.files:
                    flash('没有选择文件', 'error')
                    return redirect(request.url)
                
                file = request.files['file']
                if file.filename == '':
                    flash('没有选择文件', 'error')
                    return redirect(request.url)
                
                if file and allowed_file(file.filename):
                    filename = secure_filename(file.filename)
                    filepath = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                    file.save(filepath)
                    
                    try:
                        # 获取解密方法和顺序
                        decode_method = request.form.get('decode_method', 'all')
                        decode_order = request.form.get('decode_order', 'binary_first')
                        # 解码文件
                        decode_lua_file(filepath, decode_order, decode_method)
                        flash('文件上传并解码成功！', 'success')
                    except Exception as e:
                        logger.error(f"Error decoding file: {str(e)}")
                        flash('文件解码失败', 'error')
                    
                    return redirect(url_for('upload_file'))
                
                flash('不支持的文件类型', 'error')
                return redirect(request.url)
                
            return render_template('upload.html', decoded_files=decoded_files)
        except Exception as e:
            logger.error(f"Error in upload_file route: {str(e)}")
            return f"An error occurred: {str(e)}", 500

    @app.route('/download/<filename>')
    def download_file(filename):
        try:
            # 获取上传文件和解码文件的路径
            decoded_filepath = os.path.join(app.config['DECODED_FOLDER'], filename)
            original_filename = filename.split('decoded_', 1)[1]  # 移除'decoded_'前缀
            method = original_filename.split('_', 1)[0]  # 获取解密方法
            original_filename = original_filename.split(method + '_', 1)[1]  # 获取原始文件名
            upload_filepath = os.path.join(app.config['UPLOAD_FOLDER'], original_filename)
            
            # 发送文件
            response = send_from_directory(app.config['DECODED_FOLDER'], filename)
            
            @response.call_on_close
            def cleanup():
                try:
                    # 删除上传的原始文件
                    if os.path.exists(upload_filepath):
                        os.remove(upload_filepath)
                        logger.info(f"Deleted uploaded file: {upload_filepath}")
                    
                    # 删除所有相关的解码文件
                    decoded_pattern = f"decoded_*_{original_filename}"
                    for f in os.listdir(app.config['DECODED_FOLDER']):
                        if fnmatch.fnmatch(f, decoded_pattern):
                            os.remove(os.path.join(app.config['DECODED_FOLDER'], f))
                            logger.info(f"Deleted decoded file: {f}")
                
                except Exception as e:
                    logger.error(f"Error cleaning up files: {str(e)}")
            
            return response
            
        except Exception as e:
            logger.error(f"Error downloading file: {str(e)}")
            flash('文件下载失败', 'error')
            return redirect(url_for('upload_file'))

    def decode_lua_file(filepath, decode_order='binary_first', decode_method='all'):
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            
            def binary_decode(data):
                try:
                    # 二进制解密
                    decoded = bytearray()
                    for byte in data:
                        # 反转每个字节的位
                        reversed_byte = int('{:08b}'.format(byte)[::-1], 2)
                        decoded.append(reversed_byte)
                    return bytes(decoded)
                except Exception as e:
                    logger.error(f"Binary decode error: {str(e)}")
                    return data

            def try_all_decodes(data):
                results = []
                
                # 1. 培根密码解密
                def bacon_decode(text):
                    try:
                        # 将二进制数据转换为字符串
                        text = ''.join(chr(b) for b in text if 32 <= b <= 126)
                        # 培根密码字典
                        bacon_dict = {
                            'AAAAA': 'A', 'AAAAB': 'B', 'AAABA': 'C',
                            # ... 更多映射
                        }
                        decoded = ''
                        for i in range(0, len(text), 5):
                            chunk = text[i:i+5]
                            if chunk in bacon_dict:
                                decoded += bacon_dict[chunk]
                        return decoded.encode()
                    except:
                        return None

                # 2. 栅栏密码解密
                def rail_fence_decode(text, rails=3):
                    try:
                        length = len(text)
                        fence = [[] for _ in range(rails)]
                        rail = 0
                        direction = 1
                        
                        # 创建栅栏结构
                        for i in range(length):
                            fence[rail].append(text[i])
                            if rail == 0:
                                direction = 1
                            elif rail == rails - 1:
                                direction = -1
                            rail += direction
                        
                        return b''.join(sum(fence, []))
                    except:
                        return None

                # 3. 曲路密码解密
                def route_decode(text):
                    try:
                        size = int(len(text) ** 0.5)
                        if size * size != len(text):
                            return None
                        
                        matrix = []
                        for i in range(0, len(text), size):
                            matrix.append(text[i:i+size])
                        
                        result = bytearray()
                        # 从左上角开始螺旋读取
                        top, bottom = 0, size-1
                        left, right = 0, size-1
                        
                        while top <= bottom and left <= right:
                            for i in range(left, right + 1):
                                result.append(matrix[top][i])
                            top += 1
                            
                            for i in range(top, bottom + 1):
                                result.append(matrix[i][right])
                            right -= 1
                            
                            if top <= bottom:
                                for i in range(right, left - 1, -1):
                                    result.append(matrix[bottom][i])
                                bottom -= 1
                            
                            if left <= right:
                                for i in range(bottom, top - 1, -1):
                                    result.append(matrix[i][left])
                                left += 1
                        
                        return bytes(result)
                    except:
                        return None

                # 4. 列移位密码解密
                def columnar_decode(text, key_length=4):
                    try:
                        columns = [[] for _ in range(key_length)]
                        col = 0
                        for byte in text:
                            columns[col].append(byte)
                            col = (col + 1) % key_length
                        
                        result = bytearray()
                        for row in range(len(columns[0])):
                            for col in range(key_length):
                                if row < len(columns[col]):
                                    result.append(columns[col][row])
                        return bytes(result)
                    except:
                        return None

                # 5. 01248密码解密
                def zero1248_decode(text):
                    try:
                        # 将二进制数据转换为字符串
                        text = ''.join(chr(b) for b in text if 32 <= b <= 126)
                        result = bytearray()
                        chunks = text.split()
                        
                        for chunk in chunks:
                            value = 0
                            for digit in chunk:
                                if digit in '01248':
                                    value += int(digit)
                            result.append(value)
                        
                        return bytes(result)
                    except:
                        return None

                # 尝试所有解密方法
                methods = [
                    ('bacon', lambda: bacon_decode(data)),
                    ('rail_fence', lambda: rail_fence_decode(data)),
                    ('route', lambda: route_decode(data)),
                    ('columnar', lambda: columnar_decode(data)),
                    ('01248', lambda: zero1248_decode(data))
                ]

                for method_name, decode_func in methods:
                    try:
                        result = decode_func()
                        if result:
                            results.append((method_name, result))
                    except Exception as e:
                        logger.error(f"Error in {method_name} decode: {str(e)}")

                return results

            # 根据选择的顺序执行解密
            if decode_order == 'binary_first':
                # 先二进制解密，再其他解密
                content = binary_decode(content)
                decoded_results = try_all_decodes(content)
                prefix = 'decoded_binary_first_'
            else:
                # 先其他解密，再二进制解密
                decoded_results = try_all_decodes(content)
                # 对每个解密结果进行二进制解密
                binary_results = []
                for method, result in decoded_results:
                    binary_result = binary_decode(result)
                    binary_results.append((f"binary_last_{method}", binary_result))
                decoded_results = binary_results
                prefix = 'decoded_'

            # 保存解密结果
            for method_name, decoded_content in decoded_results:
                original_filename = os.path.basename(filepath)
                name, ext = os.path.splitext(original_filename)
                decoded_filename = f"{prefix}{method_name}_{name}{ext}"
                decoded_filepath = os.path.join(app.config['DECODED_FOLDER'], decoded_filename)
                
                with open(decoded_filepath, 'wb') as f:
                    f.write(decoded_content)
                
                logger.info(f"File decoded with {method_name}: {decoded_filepath}")

            # 如果没有其他解密方法成功，保存二进制解密结果
            if not decoded_results:
                original_filename = os.path.basename(filepath)
                name, ext = os.path.splitext(original_filename)
                decoded_filename = f"decoded_binary_{name}{ext}"
                decoded_filepath = os.path.join(app.config['DECODED_FOLDER'], decoded_filename)
                
                with open(decoded_filepath, 'wb') as f:
                    f.write(content)
                
                logger.info(f"File decoded with binary method: {decoded_filepath}")

            if decode_method == 'vm_decrypt':
                # 使用图片中的密钥
                key = "6139930228184375519"
                decoded_content = vm_decrypt(content, key)
                
                if decoded_content:
                    # 保存解密结果
                    original_filename = os.path.basename(filepath)
                    name, ext = os.path.splitext(original_filename)
                    # 如果是 .alp 文件，转换为 .lua
                    if ext.lower() == '.alp':
                        ext = '.lua'
                    decoded_filename = f"decoded_vm_{name}{ext}"
                    decoded_filepath = os.path.join(app.config['DECODED_FOLDER'], decoded_filename)
                    
                    with open(decoded_filepath, 'wb') as f:
                        f.write(decoded_content)
                    
                    logger.info(f"File decoded with VM method: {decoded_filepath}")
                else:
                    raise ValueError("VM解密失败")
                
        except Exception as e:
            logger.error(f"Error in decode_lua_file: {str(e)}")
            raise

    @app.route('/clear-files', methods=['POST'])
    def clear_files():
        try:
            # 清除上传文件夹
            for filename in os.listdir(app.config['UPLOAD_FOLDER']):
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    logger.info(f"Deleted uploaded file: {file_path}")
            
            # 清除解码文件夹
            for filename in os.listdir(app.config['DECODED_FOLDER']):
                file_path = os.path.join(app.config['DECODED_FOLDER'], filename)
                if os.path.isfile(file_path):
                    os.remove(file_path)
                    logger.info(f"Deleted decoded file: {file_path}")
            
            return jsonify({'success': True})
        except Exception as e:
            logger.error(f"Error clearing files: {str(e)}")
            return jsonify({'success': False, 'error': str(e)})

    @app.errorhandler(404)
    def not_found_error(error):
        return render_template('404.html'), 404

    @app.errorhandler(500)
    def internal_error(error):
        db.session.rollback()
        return render_template('500.html'), 500

    def vm_decrypt(data, key):
        try:
            decrypted = bytearray()
            key_length = len(key)
            
            # 将内容转换为字符串以便处理
            content = data.decode('utf-8', errors='ignore')
            
            # 获取文件长度
            length = len(content)
            
            # 解密过程
            for i in range(length):
                # 计算密钥位置
                key_digit = int(key[i % key_length]) % key_length + 1
                
                # 获取当前字符的ASCII值
                ascii_value = ord(content[i])
                
                # 根据条件进行解密
                if i % 2 == 1:
                    ascii_value = ascii_value - key_digit
                else:
                    ascii_value = ascii_value + key_digit
                    
                decrypted.append(ascii_value)
            
            return bytes(decrypted)
        except Exception as e:
            logger.error(f"VM decrypt error: {str(e)}")
            return None

    if __name__ == '__main__':
        with app.app_context():
            try:
                db.create_all()
                logger.info("Database tables created successfully")
            except Exception as e:
                logger.error(f"Error creating database tables: {str(e)}")
                raise
        app.run(debug=True)

except Exception as e:
    logger.error(f"Application startup error: {str(e)}")
    raise 
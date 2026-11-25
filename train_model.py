import pandas as pd
import joblib

from sklearn.linear_model import LinearRegression
from sklearn.linear_model import LogisticRegression

# =========================================================
# 📊 STUDENT PERFORMANCE DATASET
# =========================================================

data = pd.DataFrame({

    'attendance': [95, 90, 85, 80, 75, 70, 65, 60, 55, 50],

    'assignment': [88, 85, 80, 78, 74, 70, 66, 60, 55, 50],

    'midterm': [90, 84, 82, 79, 73, 69, 65, 61, 58, 45],

    'final_score': [92, 86, 84, 80, 76, 72, 68, 64, 60, 50]

})

# =========================================================
# 🎯 FEATURES & TARGET
# =========================================================

X = data[['attendance', 'assignment', 'midterm']]
y = data['final_score']

# =========================================================
# 🤖 TRAIN GRADE PREDICTION MODEL
# =========================================================

grade_model = LinearRegression()

grade_model.fit(X, y)

# SAVE MODEL
joblib.dump(grade_model, 'grade_model.pkl')

print("✅ Grade Prediction Model Saved")

# =========================================================
# ⚠️ WEAK STUDENT DETECTION MODEL
# =========================================================

# 1 = Weak Student
# 0 = Good Student

data['weak_student'] = data['final_score'].apply(
    lambda x: 1 if x < 60 else 0
)

X2 = data[['attendance', 'assignment', 'midterm']]
y2 = data['weak_student']

weak_model = LogisticRegression()

weak_model.fit(X2, y2)

# SAVE MODEL
joblib.dump(weak_model, 'weak_student_model.pkl')

print("✅ Weak Student Detection Model Saved")
import numpy as np

# ---------------------------------------------
# Checking boundary line crossing detection

def line(p1, p2):
  A = (p1[1] - p2[1])
  B = (p2[0] - p1[0])
  C = (p1[0]*p2[1] - p2[0]*p1[1])
  return A, B, -C

# Calcuate the coordination of intersect point of line segments - 線分同士が交差する座標を計算
def calcIntersectPoint(line1p1, line1p2, line2p1, line2p2):
  L1 = line(line1p1, line1p2)
  L2 = line(line2p1, line2p2)
  D  = L1[0] * L2[1] - L1[1] * L2[0]
  Dx = L1[2] * L2[1] - L1[1] * L2[2]
  Dy = L1[0] * L2[2] - L1[2] * L2[0]
  x = Dx / D
  y = Dy / D
  return x,y

# Check if line segments intersect - 線分同士が交差するかどうかチェック
def checkIntersect(p1, p2, p3, p4):
  tc1 = (p1[0] - p2[0]) * (p3[1] - p1[1]) + (p1[1] - p2[1]) * (p1[0] - p3[0])
  tc2 = (p1[0] - p2[0]) * (p4[1] - p1[1]) + (p1[1] - p2[1]) * (p1[0] - p4[0])
  td1 = (p3[0] - p4[0]) * (p1[1] - p3[1]) + (p3[1] - p4[1]) * (p3[0] - p1[0])
  td2 = (p3[0] - p4[0]) * (p2[1] - p3[1]) + (p3[1] - p4[1]) * (p3[0] - p2[0])
  return tc1*tc2<0 and td1*td2<0

# convert a line to a vector
# line(point1)-(point2)
def line_vectorize(point1, point2):
  a = point2[0]-point1[0]
  b = point2[1]-point1[1]
  return [a,b]

def calc_orientation(point1, point2, point3, point4):
    u = np.array(line_vectorize(point1, point2))
    v = np.array(line_vectorize(point3, point4))
    return u[0] * v[1] - u[1] * v[0] < 0

def sign(p1, p2, p3):
    return (p1[0] - p3[0]) * (p2[1] - p3[1]) - \
           (p2[0] - p3[0]) * (p1[1] - p3[1])


def point_in_triangle(point, corners):
    d1 = sign(point, corners[0, :], corners[1, :])
    d2 = sign(point, corners[1, :], corners[2, :])
    d3 = sign(point, corners[2, :], corners[0, :])

    has_neg = (d1 < 0) or (d2 < 0) or (d3 < 0)
    has_pos = (d1 > 0) or (d2 > 0) or (d3 > 0)
    return not(has_neg and has_pos)


def point_in_box(point, corners):
    triangle1 = corners[:3, :]
    # print(triangle1)
    ind = [2,3,0]
    triangle2 = corners[ind, :]
    # print(triangle2)

    is_in1 = point_in_triangle(point, triangle1)
    is_in2 = point_in_triangle(point, triangle2)

    return is_in1 or is_in2
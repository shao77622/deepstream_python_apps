# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import sys
import time
import random
from line_boundary_check import *


class boundaryLine:
    def __init__(self, line=(0, 0, 0, 0), id=-1):
        self.p0 = (line[0], line[1])
        self.p1 = (line[2], line[3])
        self.id = id
        self.color = (0, 255, 255)
        self.lineThinkness = 4
        self.textColor = (0, 255, 255)
        self.textSize = 4
        self.textThinkness = 2
        self.count1 = 0
        self.count2 = 0


# in: boundary_line = boundaryLine class object
#     trajectory   = (x1, y1, x2, y2)
def checkLineCross(boundary_line, trajectory):
    traj_p0 = (trajectory[0], trajectory[1])  # Trajectory of an object
    traj_p1 = (trajectory[2], trajectory[3])
    bLine_p0 = (boundary_line.p0[0], boundary_line.p0[1])  # Boundary line
    bLine_p1 = (boundary_line.p1[0], boundary_line.p1[1])
    intersect = checkIntersect(traj_p0, traj_p1, bLine_p0, bLine_p1)  # Check if intersect or not
    if intersect == True:
        if calc_orientation(traj_p0, traj_p1, bLine_p0, bLine_p1):
            boundary_line.count1 += 1
            print(boundary_line.id, "boundary_line.count1", boundary_line.count1)
        else:
            boundary_line.count2 += 1
            print(boundary_line.id, "boundary_line.count2", boundary_line.count2)
        # cx, cy = calcIntersectPoint(traj_p0, traj_p1, bLine_p0, bLine_p1) # Calculate the intersect coordination


# Multiple lines cross check
def checkLineCrosses(boundaryLines, objects):
    for obj in objects:
        traj = obj.trajectory
        if len(traj) > 1:
            p0 = traj[-2]
            p1 = traj[-1]
            for line in boundaryLines:
                checkLineCross(line, [p0[0], p0[1], p1[0], p1[1]])


# ------------------------------------
# Area intrusion detection
class area:
    def __init__(self, contour, id=-1):
        self.contour = np.array(contour, dtype=np.int32)
        self.id = id
        self.count = 0


# Area intrusion check
def checkAreaIntrusion(areas, objects):
    for area in areas:
        area.count = 0
        for obj in objects:
            p0 = (obj.pos[0] + obj.pos[2]) // 2
            p1 = (obj.pos[1] + obj.pos[3]) // 2
            # if cv2.pointPolygonTest(area.contour, (p0, p1), False)>=0:
            if point_in_box((p0, p1), area.contour):
                area.count += 1
                obj.intrusion = True
                intrusion_last_time = None
                if obj.intrusion_time:
                    intrusion_last_time = time.monotonic() - obj.intrusion_time
                print(area.id, "area.count", area.count, obj.pos, "intrusion last sec", intrusion_last_time)


# ------------------------------------
# Object tracking

class object:
    def __init__(self, pos, id=-1):
        self.id = id
        self.trajectory = []
        self.time = time.monotonic()
        self.intrusion = False
        self.intrusion_time = None
        self.pos = pos


class objectCacher:
    def __init__(self):
        self.timeout = 3  # sec
        self.clearDB()
        pass

    def clearDB(self):
        self.objectDB = {}

    def evictTimeoutObjectFromDB(self):
        # discard time out objects
        to_del_key_list = []
        now = time.monotonic()
        for key, value in self.objectDB.items():
            if value.time + self.timeout < now:
                to_del_key_list.append(key)
        for i in to_del_key_list:
            del self.objectDB[i]
            print("Discarded  : id {}".format(i))

    # objects = list of object class
    def cacheObjects(self, objects):
        # if no object found, skip the rest of processing
        if len(objects) == 0:
            return

        # If any object is registred in the db, update the db with the same id obj
        for obj in objects:
            objDB = self.objectDB.get(obj.id)
            if objDB is None:
                # add object that is not registred in the db
                self.objectDB[obj.id] = obj
                self.objectDB[obj.id].time = time.monotonic()
                xmin, ymin, xmax, ymax = obj.pos
                self.objectDB[obj.id].trajectory = [
                    [(xmin + xmax) // 2, (ymin + ymax) // 2]]  # position history for trajectory line
                obj.trajectory = self.objectDB[obj.id].trajectory
            else:
                objDB.time = time.monotonic()  # update last found time
                xmin, ymin, xmax, ymax = obj.pos
                objDB.trajectory.append(
                    [(xmin + xmax) // 2, (ymin + ymax) // 2])  # record position history as trajectory
                obj.trajectory = objDB.trajectory
                obj.intrusion_time = objDB.intrusion_time

    # objects = list of object class
    def updateCache(self, objects):
        # if no object found, skip the rest of processing
        if len(objects) == 0:
            return
        for obj in objects:
            objDB = self.objectDB.get(obj.id)
            if objDB:
                if obj.intrusion:
                    if objDB.intrusion_time is None:
                        objDB.intrusion_time = time.monotonic()
                else:
                    objDB.intrusion_time = None


# boundary lines
boundaryLines = [
    boundaryLine([0, 450, 1280, 450], 1),
    # boundaryLine([300, 40, 20, 400], 1),
    # boundaryLine([440, 40, 700, 400], 2)
]

# Areas
areas = [
    area([[200, 200], [500, 180], [600, 400], [300, 300]], 1)
]


def roadway_event(objects, cacher):
    # trigger in/out(car), and illegal stay(people, car, two_wheel) event:
    # entrance : [[200, 200], [400, 200], [300, 400], [100, 400]]
    # a_b_line: [[200, 200], [400, 200]]
    global boundaryLines, areas

    cacher.cacheObjects(objects)
    cacher.evictTimeoutObjectFromDB()
    checkLineCrosses(boundaryLines, objects)
    #checkAreaIntrusion(areas, objects)
    cacher.updateCache(objects)


def main():
    cacher = objectCacher()
    for i in range(1000):
        objects = []
        move = random.randint(-1000, 1000)
        # objects.append(object([200 + move, 200, 210 + move, 210], 1))
        if i % 10 == 0:
            objects.append(object([23, 200, 33, 210], 1))
            objects.append(object([33, 200, 43, 210], 2))
        else:
            objects.append(object([423, 200, 433, 210], 1))
            objects.append(object([453, 250, 463, 260], 2))

        time.sleep(1)
        # print(objects[0].pos)
        roadway_event(objects, cacher)
        if i == 999:
            pass


if __name__ == '__main__':
    sys.exit(main() or 0)

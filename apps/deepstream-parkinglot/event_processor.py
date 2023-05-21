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

import time

#time to max live without detection for roi
ROI_UPDATE_TIME_THRESHOLD = 1

class EventObject:
    def __init__(self, class_id, object_id, roiStatus, lcStatus):
        self.class_id = class_id
        self.object_id = object_id
        self.roiStatus = roiStatus
        self.roi_time = time.monotonic()
        self.update_time = time.monotonic()
        self.time_last_for_sec = 0
        self.lcStatus = lcStatus


class RoiMonitor:
    def __init__(self, roi_time_threshold):
        self.roi_time_threshold = roi_time_threshold  # sec
        self.roi_objects_previous = {}  # dict for objects in rois
        pass

    def update_roi_objects_with_previous(self, roi_objects):
        roi_objects_new = {}
        for roi_object in roi_objects:
            roi_obj_prev = self.roi_objects_previous.get(roi_object.object_id)
            if roi_obj_prev:
                roi_object.roi_time = roi_obj_prev.roi_time
                roi_object.time_last_for_sec = roi_object.update_time - roi_object.roi_time
                if roi_object.time_last_for_sec > self.roi_time_threshold:
                    self.roi_event_message_notify(roi_object)
            roi_objects_new[roi_object.object_id] = roi_object
        # keep previous objects for a while because some frame may miss detection
        for key, value in self.roi_objects_previous.items():
            if time.monotonic() - value.update_time < ROI_UPDATE_TIME_THRESHOLD and roi_objects_new.get(key) is None:
                roi_objects_new[key] = value
        self.roi_objects_previous = roi_objects_new

    def roi_event_message_notify(self, roi_object):
        print("Class {0} Object {1} roi status: {2} last for {3} sec".format(roi_object.class_id, roi_object.object_id,
                                                                             roi_object.roiStatus,
                                                                             roi_object.time_last_for_sec))


def line_crossing_event_message_notify(lc_objects):
    for lc_object in lc_objects:
        print("Class {0} Object {1} line crossing status: {2}".format(lc_object.class_id, lc_object.object_id,
                                                                      lc_object.lcStatus))

#!/usr/bin/env python3

################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2020-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
################################################################################

import sys

sys.path.append('../')
import gi
import configparser

gi.require_version('Gst', '1.0')
gi.require_version('GstRtspServer', '1.0')
gi.require_version('GstPbutils', '1.0')
from gi.repository import GLib, Gst, GstRtspServer, GstPbutils

from ctypes import *
import time
import sys
import math
import platform
from common.is_aarch_64 import is_aarch64
from common.bus_call import bus_call
from common.FPS import PERF_DATA

import event_processor

import pyds

perf_data = None

MAX_DISPLAY_LEN = 64
PGIE_CLASS_ID_VEHICLE = 0
PGIE_CLASS_ID_BICYCLE = 1
PGIE_CLASS_ID_PERSON = 2
PGIE_CLASS_ID_ROADSIGN = 3
MUXER_OUTPUT_WIDTH = 1920
MUXER_OUTPUT_HEIGHT = 1080
MUXER_BATCH_TIMEOUT_USEC = 4000000
TILED_OUTPUT_WIDTH = 1280
TILED_OUTPUT_HEIGHT = 720
GST_CAPS_FEATURES_NVMM = "memory:NVMM"
OSD_PROCESS_MODE = 0
OSD_DISPLAY_TEXT = 1

MAX_NUM_SOURCES = 2
g_source_object_dict = {}

pipeline = None
streammux = None

pgie_classes_str = ["Vehicle", "TwoWheeler", "Person", "RoadSign"]
ROI_TIME_THRESHOLD = 1
roi_monitor = event_processor.RoiMonitor(ROI_TIME_THRESHOLD)


class SourceObject:
    def __init__(self, source_id, uri, source_bin, eos_status):
        self.source_id = source_id
        self.uri = uri
        self.source_bin = source_bin
        self.eos_status = eos_status


# nvanlytics_src_pad_buffer_probe  will extract metadata received on nvtiler sink pad
# and update params for drawing rectangle, object information etc.
def nvanalytics_src_pad_buffer_probe(pad, info, u_data):
    frame_number = 0
    num_rects = 0
    gst_buffer = info.get_buffer()
    if not gst_buffer:
        print("Unable to get GstBuffer ")
        return

    # Retrieve batch metadata from the gst_buffer
    # Note that pyds.gst_buffer_get_nvds_batch_meta() expects the
    # C address of gst_buffer as input, which is obtained with hash(gst_buffer)
    batch_meta = pyds.gst_buffer_get_nvds_batch_meta(hash(gst_buffer))
    l_frame = batch_meta.frame_meta_list

    while l_frame:
        try:
            # Note that l_frame.data needs a cast to pyds.NvDsFrameMeta
            # The casting is done by pyds.NvDsFrameMeta.cast()
            # The casting also keeps ownership of the underlying memory
            # in the C code, so the Python garbage collector will leave
            # it alone.
            frame_meta = pyds.NvDsFrameMeta.cast(l_frame.data)
        except StopIteration:
            break

        frame_number = frame_meta.frame_num
        l_obj = frame_meta.obj_meta_list
        num_rects = frame_meta.num_obj_meta
        obj_counter = {
            PGIE_CLASS_ID_VEHICLE: 0,
            PGIE_CLASS_ID_PERSON: 0,
            PGIE_CLASS_ID_BICYCLE: 0,
            PGIE_CLASS_ID_ROADSIGN: 0
        }

        lc_objects = []
        roi_objects = []
        print("#" * 50)
        while l_obj:
            try:
                # Note that l_obj.data needs a cast to pyds.NvDsObjectMeta
                # The casting is done by pyds.NvDsObjectMeta.cast()
                obj_meta = pyds.NvDsObjectMeta.cast(l_obj.data)
            except StopIteration:
                break
            obj_counter[obj_meta.class_id] += 1
            l_user_meta = obj_meta.obj_user_meta_list
            lc_object = None
            roi_object = None
            # Extract object level meta data from NvDsAnalyticsObjInfo
            while l_user_meta:
                try:
                    user_meta = pyds.NvDsUserMeta.cast(l_user_meta.data)
                    if user_meta.base_meta.meta_type == pyds.nvds_get_user_meta_type("NVIDIA.DSANALYTICSOBJ.USER_META"):
                        user_meta_data = pyds.NvDsAnalyticsObjInfo.cast(user_meta.user_meta_data)
                        if user_meta_data.dirStatus: print(
                            "Object {0} moving in direction: {1}".format(obj_meta.object_id, user_meta_data.dirStatus))
                        if user_meta_data.lcStatus:
                            print("Object {0} line crossing status: {1}".format(obj_meta.object_id,
                                                                                user_meta_data.lcStatus))
                            lc_object = event_processor.EventObject(obj_meta.class_id, obj_meta.object_id, None,
                                                                    user_meta_data.lcStatus)
                        if user_meta_data.ocStatus: print(
                            "Object {0} overcrowding status: {1}".format(obj_meta.object_id, user_meta_data.ocStatus))
                        if user_meta_data.roiStatus:
                            print("Object {0} roi status: {1}".format(obj_meta.object_id, user_meta_data.roiStatus))
                            roi_object = event_processor.EventObject(obj_meta.class_id, obj_meta.object_id,
                                                                     user_meta_data.roiStatus, None)

                except StopIteration:
                    break

                try:
                    l_user_meta = l_user_meta.next
                except StopIteration:
                    break
            if lc_object:
                lc_objects.append(lc_object)
            if roi_object:
                roi_objects.append(roi_object)
            try:
                l_obj = l_obj.next
            except StopIteration:
                break

        # custom event process
        roi_monitor.update_roi_objects_with_previous(roi_objects)
        event_processor.line_crossing_event_message_notify(lc_objects)

        # Get meta data from NvDsAnalyticsFrameMeta
        l_user = frame_meta.frame_user_meta_list
        while l_user:
            try:
                user_meta = pyds.NvDsUserMeta.cast(l_user.data)
                if user_meta.base_meta.meta_type == pyds.nvds_get_user_meta_type("NVIDIA.DSANALYTICSFRAME.USER_META"):
                    user_meta_data = pyds.NvDsAnalyticsFrameMeta.cast(user_meta.user_meta_data)
                    if user_meta_data.objInROIcnt: print("Objs in ROI: {0}".format(user_meta_data.objInROIcnt))
                    if user_meta_data.objLCCumCnt: print(
                        "Linecrossing Cumulative: {0}".format(user_meta_data.objLCCumCnt))
                    if user_meta_data.objLCCurrCnt: print(
                        "Linecrossing Current Frame: {0}".format(user_meta_data.objLCCurrCnt))
                    if user_meta_data.ocStatus: print("Overcrowding status: {0}".format(user_meta_data.ocStatus))
            except StopIteration:
                break
            try:
                l_user = l_user.next
            except StopIteration:
                break

        print("source_id=", frame_meta.source_id, "Frame Number=", frame_number, "stream id=", frame_meta.pad_index,
              "Number of Objects=", num_rects, "Vehicle_count=", obj_counter[PGIE_CLASS_ID_VEHICLE], "Person_count=",
              obj_counter[PGIE_CLASS_ID_PERSON])
        # Update frame rate through this probe
        stream_index = "stream{0}".format(frame_meta.pad_index)
        global perf_data
        perf_data.update_fps(stream_index)
        try:
            l_frame = l_frame.next
        except StopIteration:
            break
        print("#" * 50)

    return Gst.PadProbeReturn.OK


def cb_newpad(decodebin, decoder_src_pad, data, source_id):
    global streammux
    print("In cb_newpad\n", source_id)
    caps = decoder_src_pad.get_current_caps()
    if not caps:
        caps = decoder_src_pad.query_caps()
    gststruct = caps.get_structure(0)
    gstname = gststruct.get_name()
    source_bin = data
    features = caps.get_features(0)

    # Need to check if the pad created by the decodebin is for video and not
    # audio.
    print("gstname=", gstname)
    if (gstname.find("video") != -1):
        padname = "sink_%u" % source_id
        sinkpad = streammux.get_request_pad(padname)
        if not sinkpad:
            sys.stderr.write("Unable to create sink pad bin \n")
        srcpad = source_bin.get_static_pad("src")
        if not srcpad:
            sys.stderr.write("Unable to create src pad bin \n")
        srcpad.link(sinkpad)

        # Link the decodebin pad only if decodebin has picked nvidia
        # decoder plugin nvdec_*. We do this by checking if the pad caps contain
        # NVMM memory features.
        print("features=", features)
        if features.contains("memory:NVMM"):
            # Get the source bin ghost pad
            bin_ghost_pad = source_bin.get_static_pad("src")
            if not bin_ghost_pad.set_target(decoder_src_pad):
                sys.stderr.write("Failed to link decoder src pad to source bin ghost pad\n")
        else:
            sys.stderr.write(" Error: Decodebin did not pick nvidia decoder plugin.\n")


def decodebin_child_added(child_proxy, Object, name, user_data):
    print("Decodebin child added:", name, "\n")
    if (name.find("decodebin") != -1):
        Object.connect("child-added", decodebin_child_added, user_data)

    if "source" in name:
        source_element = child_proxy.get_by_name("source")
        if source_element.find_property('drop-on-latency') != None:
            Object.set_property("drop-on-latency", True)


def create_source_bin(source_id, uri):
    global g_source_object_dict
    global pipeline
    print("Creating source bin")

    g_source_object_dict[source_id] = SourceObject(source_id, uri, None, True)

    discoverer = GstPbutils.Discoverer()
    try:
        info = discoverer.discover_uri(uri)
    except Exception as e:
        print("uri_name", uri, "discover error", e)
        return None
    print("uri_name", uri, "discover result is ", info.get_result())
    if info.get_result() is not GstPbutils.DiscovererResult.OK:
        return None

    # Create a source GstBin to abstract this bin's content from the rest of the
    # pipeline
    bin_name = "source-bin-%02d" % source_id
    print(bin_name)
    nbin = Gst.Bin.new(bin_name)
    if not nbin:
        sys.stderr.write(" Unable to create source bin \n")
        return None

    # Source element for reading from the uri.
    # We will use decodebin and let it figure out the container format of the
    # stream and the codec and plug the appropriate demux and decode plugins.
    uri_decode_bin = Gst.ElementFactory.make("uridecodebin", "uri-decode-bin")
    if not uri_decode_bin:
        sys.stderr.write(" Unable to create uri decode bin \n")
        return None
    # We set the input uri to the source element
    uri_decode_bin.set_property("uri", uri)
    # Connect to the "pad-added" signal of the decodebin which generates a
    # callback once a new pad for raw data has beed created by the decodebin
    uri_decode_bin.connect("pad-added", cb_newpad, nbin, source_id)
    uri_decode_bin.connect("child-added", decodebin_child_added, nbin)

    # We need to create a ghost pad for the source bin which will act as a proxy
    # for the video decoder src pad. The ghost pad will not have a target right
    # now. Once the decode bin creates the video decoder and generates the
    # cb_newpad callback, we will set the ghost pad target to the video decoder
    # src pad.
    Gst.Bin.add(nbin, uri_decode_bin)
    bin_pad = nbin.add_pad(Gst.GhostPad.new_no_target("src", Gst.PadDirection.SRC))
    if not bin_pad:
        sys.stderr.write(" Failed to add ghost pad in source bin \n")
        return None

    pipeline.add(nbin)
    g_source_object_dict[source_id] = SourceObject(source_id, uri, nbin, False)

    return nbin


def stop_release_source(source_id):
    global streammux
    global pipeline
    global g_source_object_dict

    # Attempt to change status of source to be released
    source_bin = g_source_object_dict[source_id].source_bin
    state_return = source_bin.set_state(Gst.State.NULL)

    if state_return == Gst.StateChangeReturn.SUCCESS:
        pad_name = "sink_%u" % source_id
        print(pad_name)
        # Retrieve sink pad to be released
        sinkpad = streammux.get_static_pad(pad_name)
        # Send flush stop event to the sink pad, then release from the streammux
        sinkpad.send_event(Gst.Event.new_flush_stop(False))
        streammux.release_request_pad(sinkpad)
        print("STATE CHANGE SUCCESS\n")
        # Remove the source bin from the pipeline
        pipeline.remove(source_bin)

    elif state_return == Gst.StateChangeReturn.FAILURE:
        print("STATE CHANGE FAILURE\n")

    elif state_return == Gst.StateChangeReturn.ASYNC:
        state_return = source_bin.get_state(Gst.CLOCK_TIME_NONE)
        print("state_return", state_return)
        pad_name = "sink_%u" % source_id
        print(pad_name)
        sinkpad = streammux.get_static_pad(pad_name)
        # Send flush stop event to the sink pad, then release from the streammux
        sinkpad.send_event(Gst.Event.new_flush_stop(False))
        streammux.release_request_pad(sinkpad)
        print("STATE CHANGE ASYNC\n")
        pipeline.remove(source_bin)


def add_sources(data):
    print("add_sources", data)
    global g_source_object_dict

    source_object = g_source_object_dict[data]

    print("Calling Start %d " % source_object.source_id)

    # Create a uridecode bin with the chosen source id
    source_bin = create_source_bin(source_object.source_id, source_object.uri)
    if (not source_bin):
        sys.stderr.write("Failed to create source bin. Exiting.")
        return True

    # Set state of source bin to playing
    state_return = source_bin.set_state(Gst.State.PLAYING)

    if state_return == Gst.StateChangeReturn.SUCCESS:
        print("STATE CHANGE SUCCESS\n")
    elif state_return == Gst.StateChangeReturn.FAILURE:
        print("STATE CHANGE FAILURE\n")
    elif state_return == Gst.StateChangeReturn.ASYNC:
        state_return = source_bin.get_state(Gst.CLOCK_TIME_NONE)
        print("STATE CHANGE ", state_return, "\n")
    elif state_return == Gst.StateChangeReturn.NO_PREROLL:
        print("STATE CHANGE NO PREROLL\n")

    # will not continue schedule
    return False


def bus_call(bus, message, loop):
    global g_source_object_dict
    t = message.type
    if t == Gst.MessageType.EOS:
        sys.stdout.write("End-of-stream\n")
        loop.quit()
    elif t == Gst.MessageType.WARNING:
        err, debug = message.parse_warning()
        sys.stderr.write("Warning: %s: %s\n" % (err, debug))
    elif t == Gst.MessageType.ERROR:
        err, debug = message.parse_error()
        sys.stderr.write("Error: %s: %s\n" % (err, debug))
        loop.quit()
    elif t == Gst.MessageType.ELEMENT:
        struct = message.get_structure()
        # Check for stream-eos message
        if struct is not None and struct.has_name("stream-eos"):
            parsed, stream_id = struct.get_uint("stream-id")
            if parsed:
                # Set eos status of stream to True, to be deleted in delete-sources
                print("Got EOS from stream %d" % stream_id)
                g_source_object_dict[stream_id].eos_status = True
                stop_release_source(stream_id)
                GLib.timeout_add_seconds(20, add_sources, stream_id)
    return True


def main(args):
    global pipeline
    global streammux
    global g_source_object_dict

    # Check input arguments
    if len(args) < 2:
        sys.stderr.write("usage: %s <uri1> [uri2] ... [uriN]\n" % args[0])
        sys.exit(1)

    global perf_data
    perf_data = PERF_DATA(len(args) - 1)
    number_sources = len(args) - 1

    # Standard GStreamer initialization
    Gst.init(None)

    # Create gstreamer elements */
    # Create Pipeline element that will form a connection of other elements
    print("Creating Pipeline \n ")
    pipeline = Gst.Pipeline()
    is_live = False

    if not pipeline:
        sys.stderr.write(" Unable to create Pipeline \n")
    print("Creating streammux \n ")

    # Create nvstreammux instance to form batches from one or more sources.
    streammux = Gst.ElementFactory.make("nvstreammux", "Stream-muxer")
    if not streammux:
        sys.stderr.write(" Unable to create NvStreamMux \n")

    pipeline.add(streammux)
    for i in range(number_sources):
        print("Creating source_bin ", i, " \n ")
        uri_name = args[i + 1]
        if uri_name.find("rtsp://") == 0:
            is_live = True
        source_bin = create_source_bin(i, uri_name)
        if not source_bin:
            sys.stderr.write("Unable to create source bin \n")
            continue

    queue1 = Gst.ElementFactory.make("queue", "queue1")
    queue2 = Gst.ElementFactory.make("queue", "queue2")
    queue3 = Gst.ElementFactory.make("queue", "queue3")
    queue4 = Gst.ElementFactory.make("queue", "queue4")
    queue5 = Gst.ElementFactory.make("queue", "queue5")
    queue6 = Gst.ElementFactory.make("queue", "queue6")
    queue7 = Gst.ElementFactory.make("queue", "queue7")
    queue8 = Gst.ElementFactory.make("queue", "queue8")
    queue9 = Gst.ElementFactory.make("queue", "queue9")
    queue10 = Gst.ElementFactory.make("queue", "queue10")
    queue11 = Gst.ElementFactory.make("queue", "queue11")
    pipeline.add(queue1)
    pipeline.add(queue2)
    pipeline.add(queue3)
    pipeline.add(queue4)
    pipeline.add(queue5)
    pipeline.add(queue6)
    pipeline.add(queue7)
    pipeline.add(queue8)
    pipeline.add(queue9)
    pipeline.add(queue10)
    pipeline.add(queue11)

    print("Creating Pgie \n ")
    pgie = Gst.ElementFactory.make("nvinfer", "primary-inference")
    if not pgie:
        sys.stderr.write(" Unable to create pgie \n")

    print("Creating nvtracker \n ")
    tracker = Gst.ElementFactory.make("nvtracker", "tracker")
    if not tracker:
        sys.stderr.write(" Unable to create tracker \n")

    print("Creating nvdsanalytics \n ")
    nvanalytics = Gst.ElementFactory.make("nvdsanalytics", "analytics")
    if not nvanalytics:
        sys.stderr.write(" Unable to create nvanalytics \n")
    nvanalytics.set_property("config-file", "config_nvdsanalytics.txt")

    print("Creating tiler \n ")
    tiler = Gst.ElementFactory.make("nvmultistreamtiler", "nvtiler")
    if not tiler:
        sys.stderr.write(" Unable to create tiler \n")

    print("Creating nvvidconv \n ")
    nvvidconv = Gst.ElementFactory.make("nvvideoconvert", "convertor")
    if not nvvidconv:
        sys.stderr.write(" Unable to create nvvidconv \n")

    print("Creating nvosd \n ")
    nvosd = Gst.ElementFactory.make("nvdsosd", "onscreendisplay")
    if not nvosd:
        sys.stderr.write(" Unable to create nvosd \n")
    nvosd.set_property('process-mode', OSD_PROCESS_MODE)
    nvosd.set_property('display-text', OSD_DISPLAY_TEXT)

    nvvidconv_postosd = Gst.ElementFactory.make("nvvideoconvert", "convertor_postosd")
    if not nvvidconv_postosd:
        sys.stderr.write(" Unable to create nvvidconv_postosd \n")

    # Create a caps filter
    caps = Gst.ElementFactory.make("capsfilter", "filter")
    caps.set_property("caps", Gst.Caps.from_string("video/x-raw(memory:NVMM), format=I420"))

    # Make the encoder
    encoder = Gst.ElementFactory.make("nvv4l2h264enc", "encoder")
    print("Creating H264 Encoder")
    if not encoder:
        sys.stderr.write(" Unable to create encoder")
    encoder.set_property('bitrate', 4000000)
    if is_aarch64():
        encoder.set_property('preset-level', 1)
        encoder.set_property('insert-sps-pps', 1)
        # encoder.set_property('bufapi-version', 1)

    # Make the payload-encode video into RTP packets
    rtppay = Gst.ElementFactory.make("rtph264pay", "rtppay")
    print("Creating H264 rtppay")
    if not rtppay:
        sys.stderr.write(" Unable to create rtppay")

    # Make the UDP sink
    updsink_port_num = 5400
    sink = Gst.ElementFactory.make("udpsink", "udpsink")
    if not sink:
        sys.stderr.write(" Unable to create udpsink")

    sink.set_property('host', '224.224.255.255')
    sink.set_property('port', updsink_port_num)
    sink.set_property('async', False)
    sink.set_property('sync', 1)

    if is_live:
        print("Atleast one of the sources is live")
        streammux.set_property('live-source', 1)

    streammux.set_property('width', 1920)
    streammux.set_property('height', 1080)
    streammux.set_property('batch-size', MAX_NUM_SOURCES * 2)
    streammux.set_property('batched-push-timeout', 25000)
    pgie.set_property('config-file-path', "parkinglot_pgie_config.txt")
    pgie_batch_size = pgie.get_property("batch-size")
    if (pgie_batch_size != MAX_NUM_SOURCES):
        print("WARNING: Overriding infer-config batch-size", pgie_batch_size, " with number of sources ",
              MAX_NUM_SOURCES, " \n")
        pgie.set_property("batch-size", MAX_NUM_SOURCES)
    tiler_rows = int(math.sqrt(MAX_NUM_SOURCES))
    tiler_columns = int(math.ceil((1.0 * MAX_NUM_SOURCES) / tiler_rows))
    tiler.set_property("rows", tiler_rows)
    tiler.set_property("columns", tiler_columns)
    tiler.set_property("width", TILED_OUTPUT_WIDTH)
    tiler.set_property("height", TILED_OUTPUT_HEIGHT)
    sink.set_property("qos", 0)

    # Set properties of tracker
    config = configparser.ConfigParser()
    config.read('parkinglot_tracker_config.txt')
    config.sections()

    for key in config['tracker']:
        if key == 'tracker-width':
            tracker_width = config.getint('tracker', key)
            tracker.set_property('tracker-width', tracker_width)
        if key == 'tracker-height':
            tracker_height = config.getint('tracker', key)
            tracker.set_property('tracker-height', tracker_height)
        if key == 'gpu-id':
            tracker_gpu_id = config.getint('tracker', key)
            tracker.set_property('gpu_id', tracker_gpu_id)
        if key == 'll-lib-file':
            tracker_ll_lib_file = config.get('tracker', key)
            tracker.set_property('ll-lib-file', tracker_ll_lib_file)
        if key == 'll-config-file':
            tracker_ll_config_file = config.get('tracker', key)
            tracker.set_property('ll-config-file', tracker_ll_config_file)
        if key == 'enable-batch-process':
            tracker_enable_batch_process = config.getint('tracker', key)
            tracker.set_property('enable_batch_process', tracker_enable_batch_process)
        if key == 'enable-past-frame':
            tracker_enable_past_frame = config.getint('tracker', key)
            tracker.set_property('enable_past_frame', tracker_enable_past_frame)

    print("Adding elements to Pipeline \n")
    pipeline.add(pgie)
    pipeline.add(tracker)
    pipeline.add(nvanalytics)
    pipeline.add(tiler)
    pipeline.add(nvvidconv)
    pipeline.add(nvosd)
    pipeline.add(nvvidconv_postosd)
    pipeline.add(caps)
    pipeline.add(encoder)
    pipeline.add(rtppay)
    pipeline.add(sink)

    # We link elements in the following order:
    # sourcebin -> streammux -> nvinfer -> nvtracker -> nvdsanalytics ->
    # nvtiler -> nvvideoconvert -> nvdsosd -> nvvidconv_postosd -> caps ->
    # encoder -> rtppay -> sink
    print("Linking elements in the Pipeline \n")
    streammux.link(queue1)
    queue1.link(pgie)
    pgie.link(queue2)
    queue2.link(tracker)
    tracker.link(queue3)
    queue3.link(nvanalytics)
    nvanalytics.link(queue4)
    queue4.link(tiler)
    tiler.link(queue5)
    queue5.link(nvvidconv)
    nvvidconv.link(queue6)
    queue6.link(nvosd)
    nvosd.link(queue7)
    queue7.link(nvvidconv_postosd)
    nvvidconv_postosd.link(queue8)
    queue8.link(caps)
    caps.link(queue9)
    queue9.link(encoder)
    encoder.link(queue10)
    queue10.link(rtppay)
    rtppay.link(queue11)
    queue11.link(sink)

    # create an event loop and feed gstreamer bus mesages to it
    loop = GLib.MainLoop()
    bus = pipeline.get_bus()
    bus.add_signal_watch()
    bus.connect("message", bus_call, loop)

    # Start streaming
    rtsp_port_num = 8555

    server = GstRtspServer.RTSPServer.new()
    server.props.service = "%d" % rtsp_port_num
    server.attach(None)

    factory = GstRtspServer.RTSPMediaFactory.new()
    factory.set_launch(
        "( udpsrc name=pay0 port=%d buffer-size=524288 caps=\"application/x-rtp, media=video, clock-rate=90000, encoding-name=(string)%s, payload=96 \" )" % (
        updsink_port_num, "H264"))
    factory.set_shared(True)
    server.get_mount_points().add_factory("/ds-test", factory)

    print("\n *** DeepStream: Launched RTSP Streaming at rtsp://localhost:%d/ds-test ***\n\n" % rtsp_port_num)

    nvanalytics_src_pad = nvanalytics.get_static_pad("src")
    if not nvanalytics_src_pad:
        sys.stderr.write(" Unable to get src pad \n")
    else:
        # nvanalytics_src_pad.add_probe(Gst.PadProbeType.BUFFER, nvanalytics_src_pad_buffer_probe, 0)
        # perf callback function to print fps every 5 sec
        GLib.timeout_add(5000, perf_data.perf_print_callback)

    # List the sources
    print("Now playing...")
    for i, source in enumerate(args):
        if (i != 0):
            print(i, ": ", source)

    print("Starting pipeline \n")
    # start play back and listed to events		
    pipeline.set_state(Gst.State.PLAYING)

    # GLib.timeout_add_seconds(10, add_sources, args[1])

    try:
        loop.run()
    except:
        pass
    # cleanup
    print("Exiting app\n")
    pipeline.set_state(Gst.State.NULL)


if __name__ == '__main__':
    sys.exit(main(sys.argv))

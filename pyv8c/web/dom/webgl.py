"""WebGL 1 and WebGL 2 rendering contexts.

No GL backend sits behind these.  Identity, constants, limits, extensions, object
creation and the state a page can query after calling into the API answer as Chrome
does; a drawing call is accepted and has no effect.  Shader sources are kept but never
compiled, so a query about a shader or program answers for one that compiled and linked.

The pname tables and the extension lists were measured on Chrome 153.
"""
from __future__ import annotations

import pyv8

from ...idl import interface
from ...foundation.arguments import MISSING, dom_string, is_nullish, missing_error


#: ``(name, constant, value)`` for every pname Chrome answers with a scalar; ``None``
#: stands for Chrome's ``null``.
_PARAMETERS = (
    ("BLEND_EQUATION", 32777, 32774),
    ("BLEND_EQUATION_RGB", 32777, 32774),
    ("BLEND_EQUATION_ALPHA", 34877, 32774),
    ("BLEND_DST_RGB", 32968, 0),
    ("BLEND_SRC_RGB", 32969, 1),
    ("BLEND_DST_ALPHA", 32970, 0),
    ("BLEND_SRC_ALPHA", 32971, 1),
    ("CULL_FACE", 2884, False),
    ("BLEND", 3042, False),
    ("DITHER", 3024, True),
    ("STENCIL_TEST", 2960, False),
    ("DEPTH_TEST", 2929, False),
    ("SCISSOR_TEST", 3089, False),
    ("POLYGON_OFFSET_FILL", 32823, False),
    ("SAMPLE_ALPHA_TO_COVERAGE", 32926, False),
    ("SAMPLE_COVERAGE", 32928, False),
    ("LINE_WIDTH", 2849, 1),
    ("CULL_FACE_MODE", 2885, 1029),
    ("FRONT_FACE", 2886, 2305),
    ("DEPTH_WRITEMASK", 2930, True),
    ("DEPTH_CLEAR_VALUE", 2931, 1),
    ("DEPTH_FUNC", 2932, 513),
    ("STENCIL_CLEAR_VALUE", 2961, 0),
    ("STENCIL_FUNC", 2962, 519),
    ("STENCIL_FAIL", 2964, 7680),
    ("STENCIL_PASS_DEPTH_FAIL", 2965, 7680),
    ("STENCIL_PASS_DEPTH_PASS", 2966, 7680),
    ("STENCIL_REF", 2967, 0),
    ("STENCIL_VALUE_MASK", 2963, 4294967295),
    ("STENCIL_WRITEMASK", 2968, 4294967295),
    ("STENCIL_BACK_FUNC", 34816, 519),
    ("STENCIL_BACK_FAIL", 34817, 7680),
    ("STENCIL_BACK_PASS_DEPTH_FAIL", 34818, 7680),
    ("STENCIL_BACK_PASS_DEPTH_PASS", 34819, 7680),
    ("STENCIL_BACK_REF", 36003, 0),
    ("STENCIL_BACK_VALUE_MASK", 36004, 4294967295),
    ("STENCIL_BACK_WRITEMASK", 36005, 4294967295),
    ("UNPACK_ALIGNMENT", 3317, 4),
    ("PACK_ALIGNMENT", 3333, 4),
    ("MAX_TEXTURE_SIZE", 3379, 16384),
    ("SUBPIXEL_BITS", 3408, 4),
    ("RED_BITS", 3410, 8),
    ("GREEN_BITS", 3411, 8),
    ("BLUE_BITS", 3412, 8),
    ("ALPHA_BITS", 3413, 8),
    ("DEPTH_BITS", 3414, 24),
    ("STENCIL_BITS", 3415, 0),
    ("POLYGON_OFFSET_UNITS", 10752, 0),
    ("POLYGON_OFFSET_FACTOR", 32824, 0),
    ("SAMPLE_BUFFERS", 32936, 1),
    ("SAMPLES", 32937, 4),
    ("SAMPLE_COVERAGE_VALUE", 32938, 1),
    ("SAMPLE_COVERAGE_INVERT", 32939, False),
    ("GENERATE_MIPMAP_HINT", 33170, 4352),
    ("MAX_VERTEX_ATTRIBS", 34921, 16),
    ("MAX_VERTEX_UNIFORM_VECTORS", 36347, 4095),
    ("MAX_VARYING_VECTORS", 36348, 30),
    ("MAX_COMBINED_TEXTURE_IMAGE_UNITS", 35661, 32),
    ("MAX_VERTEX_TEXTURE_IMAGE_UNITS", 35660, 16),
    ("MAX_TEXTURE_IMAGE_UNITS", 34930, 16),
    ("MAX_FRAGMENT_UNIFORM_VECTORS", 36349, 1024),
    ("MAX_CUBE_MAP_TEXTURE_SIZE", 34076, 16384),
    ("ACTIVE_TEXTURE", 34016, 33984),
    ("IMPLEMENTATION_COLOR_READ_TYPE", 35738, 5121),
    ("IMPLEMENTATION_COLOR_READ_FORMAT", 35739, 6408),
    ("MAX_RENDERBUFFER_SIZE", 34024, 16384),
    ("UNPACK_FLIP_Y_WEBGL", 37440, False),
    ("UNPACK_PREMULTIPLY_ALPHA_WEBGL", 37441, False),
    ("UNPACK_COLORSPACE_CONVERSION_WEBGL", 37443, 37444),
)


#: ``(name, constant, typed-array kind, value)`` for the pnames answered with an array.
_ARRAY_PARAMETERS = (
    ("BLEND_COLOR", 32773, pyv8.ArrayType.Float32Array, (0, 0, 0, 0)),
    ("ALIASED_POINT_SIZE_RANGE", 33901, pyv8.ArrayType.Float32Array, (1, 1024)),
    ("ALIASED_LINE_WIDTH_RANGE", 33902, pyv8.ArrayType.Float32Array, (1, 1)),
    ("DEPTH_RANGE", 2928, pyv8.ArrayType.Float32Array, (0, 1)),
    ("COLOR_CLEAR_VALUE", 3106, pyv8.ArrayType.Float32Array, (0, 0, 0, 0)),
    ("MAX_VIEWPORT_DIMS", 3386, pyv8.ArrayType.Int32Array, (32767, 32767)),
    ("VIEWPORT", 2978, pyv8.ArrayType.Int32Array, None),
    ("SCISSOR_BOX", 3088, pyv8.ArrayType.Int32Array, None),
    ("COMPRESSED_TEXTURE_FORMATS", 34467, pyv8.ArrayType.Uint32Array, ()),
)


#: ``(name, constant, text)`` for the pnames Chrome answers with a string.
_STRINGS = (
    ("VERSION", 7938, "WebGL 1.0 (OpenGL ES 2.0 Chromium)"),
    ("VENDOR", 7936, "WebKit"),
    ("RENDERER", 7937, "WebKit WebGL"),
    ("SHADING_LANGUAGE_VERSION", 35724, "WebGL GLSL ES 1.0 (OpenGL ES GLSL ES 1.0 Chromium)"),
)


#: The same, for the pnames a WebGL 2 context answers with its own text.
_WEBGL2_STRINGS = (
    ("VERSION", 7938, "WebGL 2.0 (OpenGL ES 3.0 Chromium)"),
    ("VENDOR", 7936, "WebKit"),
    ("RENDERER", 7937, "WebKit WebGL"),
    ("SHADING_LANGUAGE_VERSION", 35724,
     "WebGL GLSL ES 3.00 (OpenGL ES GLSL ES 3.0 Chromium)"),
)


#: ``(name, constant, value)`` for the pnames a WebGL 2 context answers differently.
#: ``None`` is a valid pname Chrome answers with ``null``, and no error.
_WEBGL2_PARAMETERS = (
    ("DRAW_BUFFER0", 34853, 1029),
    ("DRAW_BUFFER1", 34854, 1029),
    ("DRAW_BUFFER2", 34855, 1029),
    ("DRAW_BUFFER3", 34856, 1029),
    ("DRAW_BUFFER4", 34857, 1029),
    ("DRAW_BUFFER5", 34858, 1029),
    ("DRAW_BUFFER6", 34859, 1029),
    ("DRAW_BUFFER7", 34860, 1029),
    ("FRAGMENT_SHADER_DERIVATIVE_HINT", 35723, 4352),
    ("MAX_3D_TEXTURE_SIZE", 32883, 2048),
    ("MAX_ARRAY_TEXTURE_LAYERS", 35071, 2048),
    ("MAX_CLIENT_WAIT_TIMEOUT_WEBGL", 37447, 0),
    ("MAX_COLOR_ATTACHMENTS", 36063, 8),
    ("MAX_COMBINED_FRAGMENT_UNIFORM_COMPONENTS", 35379, 200704),
    ("MAX_COMBINED_UNIFORM_BLOCKS", 35374, 24),
    ("MAX_COMBINED_VERTEX_UNIFORM_COMPONENTS", 35377, 212988),
    ("MAX_DRAW_BUFFERS", 34852, 8),
    ("MAX_ELEMENTS_INDICES", 33001, 2147483647),
    ("MAX_ELEMENTS_VERTICES", 33000, 2147483647),
    ("MAX_ELEMENT_INDEX", 36203, 4294967294),
    ("MAX_FRAGMENT_INPUT_COMPONENTS", 37157, 120),
    ("MAX_FRAGMENT_UNIFORM_BLOCKS", 35373, 12),
    ("MAX_FRAGMENT_UNIFORM_COMPONENTS", 35657, 4096),
    ("MAX_PROGRAM_TEXEL_OFFSET", 35077, 7),
    ("MAX_SAMPLES", 36183, 8),
    ("MAX_SERVER_WAIT_TIMEOUT", 37137, 0),
    ("MAX_TEXTURE_LOD_BIAS", 34045, 2),
    ("MAX_TRANSFORM_FEEDBACK_INTERLEAVED_COMPONENTS", 35978, 120),
    ("MAX_TRANSFORM_FEEDBACK_SEPARATE_ATTRIBS", 35979, 4),
    ("MAX_TRANSFORM_FEEDBACK_SEPARATE_COMPONENTS", 35968, 4),
    ("MAX_UNIFORM_BLOCK_SIZE", 35376, 65536),
    ("MAX_UNIFORM_BUFFER_BINDINGS", 35375, 24),
    ("MAX_VARYING_COMPONENTS", 35659, 120),
    ("MAX_VERTEX_OUTPUT_COMPONENTS", 37154, 120),
    ("MAX_VERTEX_UNIFORM_BLOCKS", 35371, 12),
    ("MAX_VERTEX_UNIFORM_COMPONENTS", 35658, 16380),
    ("MIN_PROGRAM_TEXEL_OFFSET", 35076, -8),
    ("PACK_ROW_LENGTH", 3330, 0),
    ("PACK_SKIP_PIXELS", 3332, 0),
    ("PACK_SKIP_ROWS", 3331, 0),
    ("RASTERIZER_DISCARD", 35977, False),
    ("READ_BUFFER", 3074, 1029),
    ("TRANSFORM_FEEDBACK_ACTIVE", 36388, False),
    ("TRANSFORM_FEEDBACK_PAUSED", 36387, False),
    ("UNIFORM_BUFFER_OFFSET_ALIGNMENT", 35380, 256),
    ("UNPACK_IMAGE_HEIGHT", 32878, 0),
    ("UNPACK_ROW_LENGTH", 3314, 0),
    ("UNPACK_SKIP_IMAGES", 32877, 0),
    ("UNPACK_SKIP_PIXELS", 3316, 0),
    ("UNPACK_SKIP_ROWS", 3315, 0),
    ("COPY_READ_BUFFER", 36662, None),
    ("COPY_WRITE_BUFFER", 36663, None),
    ("PIXEL_PACK_BUFFER_BINDING", 35053, None),
    ("PIXEL_UNPACK_BUFFER_BINDING", 35055, None),
    ("READ_FRAMEBUFFER_BINDING", 36010, None),
    ("SAMPLER_BINDING", 35097, None),
    ("TEXTURE_BINDING_2D_ARRAY", 35869, None),
    ("TEXTURE_BINDING_3D", 32874, None),
    ("TRANSFORM_FEEDBACK_BINDING", 36389, None),
    ("TRANSFORM_FEEDBACK_BUFFER_BINDING", 35983, None),
    ("UNIFORM_BUFFER_BINDING", 35368, None),
    ("VERTEX_ARRAY_BINDING", 34229, None),
)


#: ``(GL name, interface)`` for every extension a WebGL 1 context lists, in the order
#: Chrome 153 answers ``getSupportedExtensions`` with.  Each name is obtainable.
_WEBGL1_EXTENSIONS = (
    ("ANGLE_instanced_arrays", "ANGLEInstancedArrays"),
    ("EXT_blend_minmax", "EXTBlendMinMax"),
    ("EXT_clip_control", "EXTClipControl"),
    ("EXT_color_buffer_half_float", "EXTColorBufferHalfFloat"),
    ("EXT_depth_clamp", "EXTDepthClamp"),
    ("EXT_disjoint_timer_query", "EXTDisjointTimerQuery"),
    ("EXT_float_blend", "EXTFloatBlend"),
    ("EXT_frag_depth", "EXTFragDepth"),
    ("EXT_polygon_offset_clamp", "EXTPolygonOffsetClamp"),
    ("EXT_shader_texture_lod", "EXTShaderTextureLOD"),
    ("EXT_texture_compression_bptc", "EXTTextureCompressionBPTC"),
    ("EXT_texture_compression_rgtc", "EXTTextureCompressionRGTC"),
    ("EXT_texture_filter_anisotropic", "EXTTextureFilterAnisotropic"),
    ("EXT_texture_mirror_clamp_to_edge", "EXTTextureMirrorClampToEdge"),
    ("EXT_sRGB", "EXTsRGB"),
    ("KHR_parallel_shader_compile", "KHRParallelShaderCompile"),
    ("OES_element_index_uint", "OESElementIndexUint"),
    ("OES_fbo_render_mipmap", "OESFboRenderMipmap"),
    ("OES_standard_derivatives", "OESStandardDerivatives"),
    ("OES_texture_float", "OESTextureFloat"),
    ("OES_texture_float_linear", "OESTextureFloatLinear"),
    ("OES_texture_half_float", "OESTextureHalfFloat"),
    ("OES_texture_half_float_linear", "OESTextureHalfFloatLinear"),
    ("OES_vertex_array_object", "OESVertexArrayObject"),
    ("WEBGL_blend_func_extended", "WebGLBlendFuncExtended"),
    ("WEBGL_color_buffer_float", "WebGLColorBufferFloat"),
    ("WEBGL_compressed_texture_s3tc", "WebGLCompressedTextureS3TC"),
    ("WEBGL_compressed_texture_s3tc_srgb", "WebGLCompressedTextureS3TCsRGB"),
    ("WEBGL_debug_renderer_info", "WebGLDebugRendererInfo"),
    ("WEBGL_debug_shaders", "WebGLDebugShaders"),
    ("WEBGL_depth_texture", "WebGLDepthTexture"),
    ("WEBGL_draw_buffers", "WebGLDrawBuffers"),
    ("WEBGL_lose_context", "WebGLLoseContext"),
    ("WEBGL_multi_draw", "WebGLMultiDraw"),
    ("WEBGL_polygon_mode", "WebGLPolygonMode"),
)


#: The same for a WebGL 2 context: what it adds, minus what WebGL 2 makes core.
_WEBGL2_EXTENSIONS = (
    ("EXT_clip_control", "EXTClipControl"),
    ("EXT_color_buffer_float", "EXTColorBufferFloat"),
    ("EXT_color_buffer_half_float", "EXTColorBufferHalfFloat"),
    ("EXT_conservative_depth", "EXTConservativeDepth"),
    ("EXT_depth_clamp", "EXTDepthClamp"),
    ("EXT_disjoint_timer_query_webgl2", "EXTDisjointTimerQueryWebGL2"),
    ("EXT_float_blend", "EXTFloatBlend"),
    ("EXT_polygon_offset_clamp", "EXTPolygonOffsetClamp"),
    ("EXT_render_snorm", "EXTRenderSnorm"),
    ("EXT_texture_compression_bptc", "EXTTextureCompressionBPTC"),
    ("EXT_texture_compression_rgtc", "EXTTextureCompressionRGTC"),
    ("EXT_texture_filter_anisotropic", "EXTTextureFilterAnisotropic"),
    ("EXT_texture_mirror_clamp_to_edge", "EXTTextureMirrorClampToEdge"),
    ("EXT_texture_norm16", "EXTTextureNorm16"),
    ("KHR_parallel_shader_compile", "KHRParallelShaderCompile"),
    ("NV_shader_noperspective_interpolation", "NVShaderNoperspectiveInterpolation"),
    ("OES_draw_buffers_indexed", "OESDrawBuffersIndexed"),
    ("OES_sample_variables", "OESSampleVariables"),
    ("OES_shader_multisample_interpolation", "OESShaderMultisampleInterpolation"),
    ("OES_texture_float_linear", "OESTextureFloatLinear"),
    ("OVR_multiview2", "OVRMultiview2"),
    ("WEBGL_blend_func_extended", "WebGLBlendFuncExtended"),
    ("WEBGL_clip_cull_distance", "WebGLClipCullDistance"),
    ("WEBGL_compressed_texture_s3tc", "WebGLCompressedTextureS3TC"),
    ("WEBGL_compressed_texture_s3tc_srgb", "WebGLCompressedTextureS3TCsRGB"),
    ("WEBGL_debug_renderer_info", "WebGLDebugRendererInfo"),
    ("WEBGL_debug_shaders", "WebGLDebugShaders"),
    ("WEBGL_lose_context", "WebGLLoseContext"),
    ("WEBGL_multi_draw", "WebGLMultiDraw"),
    ("WEBGL_polygon_mode", "WebGLPolygonMode"),
    ("WEBGL_provoking_vertex", "WebGLProvokingVertex"),
    ("WEBGL_stencil_texturing", "WebGLStencilTexturing"),
)

_WEBGL1_EXTENSION_MAP = dict(_WEBGL1_EXTENSIONS)
_WEBGL2_EXTENSION_MAP = dict(_WEBGL2_EXTENSIONS)


#: The two pnames ``WEBGL_debug_renderer_info`` answers with, taken from the profile:
#: a machine's GL identity is a collected fact, the way its screen or plugins are.
_UNMASKED_VENDOR_WEBGL = 37445
_UNMASKED_RENDERER_WEBGL = 37446


#: Members whose result is undefined, with the argument count Chrome requires.
_VOID_MEMBERS = (
    ("activeTexture", 1), ("bindAttribLocation", 3), ("blendColor", 4), ("blendEquation", 1),
    ("blendEquationSeparate", 2), ("blendFunc", 2), ("blendFuncSeparate", 4),
    ("bufferSubData", 3), ("clearDepth", 1), ("clearStencil", 1), ("compileShader", 1),
    ("compressedTexImage2D", 7), ("compressedTexSubImage2D", 8), ("copyTexImage2D", 8),
    ("copyTexSubImage2D", 8), ("cullFace", 1), ("deleteBuffer", 1), ("deleteFramebuffer", 1),
    ("deleteProgram", 1), ("deleteRenderbuffer", 1), ("deleteShader", 1), ("deleteTexture", 1),
    ("depthFunc", 1), ("depthMask", 1), ("depthRange", 2), ("disableVertexAttribArray", 1),
    ("drawArrays", 3), ("drawElements", 4), ("drawingBufferStorage", 3),
    ("enableVertexAttribArray", 1), ("finish", 0), ("flush", 0), ("frontFace", 1),
    ("generateMipmap", 1), ("hint", 2), ("lineWidth", 1), ("makeXRCompatible", 0),
    ("pixelStorei", 2), ("polygonOffset", 2), ("renderbufferStorage", 4), ("sampleCoverage", 2),
    ("stencilFunc", 3), ("stencilFuncSeparate", 4), ("stencilMask", 1),
    ("stencilMaskSeparate", 2), ("stencilOp", 3),     ("stencilOpSeparate", 4),
    # Absent: this release installs neither; ``fn_<name>`` binds them if one does.
    ("texImage2D", 6),
    ("texParameterf", 3), ("texParameteri", 3), ("texSubImage2D", 7),
    ("uniform1f", 2), ("uniform1fv", 2), ("uniform1i", 2), ("uniform1iv", 2),
    ("uniform2f", 3), ("uniform2fv", 2), ("uniform2i", 3), ("uniform2iv", 2),
    ("uniform3f", 4), ("uniform3fv", 2), ("uniform3i", 4), ("uniform3iv", 2),
    ("uniform4f", 5), ("uniform4fv", 2), ("uniform4i", 5), ("uniform4iv", 2),
    ("uniformMatrix2fv", 3), ("uniformMatrix3fv", 3), ("uniformMatrix4fv", 3),
    ("validateProgram", 1), ("vertexAttrib1f", 2), ("vertexAttrib1fv", 2),
    ("vertexAttrib2f", 3), ("vertexAttrib2fv", 2), ("vertexAttrib3f", 4),
    ("vertexAttrib3fv", 2), ("vertexAttrib4f", 5), ("vertexAttrib4fv", 2),
    ("vertexAttribPointer", 6),
)


#: Members answered from the context' own state, with the argument count Chrome requires.
_STATE_MEMBERS = (
    ("attachShader", 2), ("bindBuffer", 2), ("bindFramebuffer", 2), ("bindRenderbuffer", 2),
    ("bindTexture", 2), ("bufferData", 3), ("checkFramebufferStatus", 1), ("clear", 1),
    ("clearColor", 4), ("colorMask", 4), ("createBuffer", 0),
    ("createFramebuffer", 0), ("createProgram", 0), ("createRenderbuffer", 0),
    ("createShader", 1), ("createTexture", 0), ("detachShader", 2), ("disable", 1),
    ("enable", 1), ("framebufferRenderbuffer", 4), ("framebufferTexture2D", 5),
    ("getActiveAttrib", 2), ("getActiveUniform", 2), ("getAttachedShaders", 1),
    ("getAttribLocation", 2), ("getBufferParameter", 2), ("getContextAttributes", 0),
    ("getError", 0), ("getExtension", 1), ("getFramebufferAttachmentParameter", 3),
    ("getParameter", 1), ("getProgramInfoLog", 1), ("getProgramParameter", 2),
    ("getRenderbufferParameter", 2), ("getShaderInfoLog", 1), ("getShaderParameter", 2),
    ("getShaderPrecisionFormat", 2), ("getShaderSource", 1), ("getSupportedExtensions", 0),
    ("getTexParameter", 2), ("getUniform", 2), ("getUniformLocation", 2),
    ("getVertexAttrib", 2), ("getVertexAttribOffset", 2), ("isBuffer", 1),
    ("isContextLost", 0), ("isEnabled", 1), ("isFramebuffer", 1), ("isProgram", 1),
    ("isRenderbuffer", 1), ("isShader", 1), ("isTexture", 1), ("linkProgram", 1),
    ("readPixels", 7), ("scissor", 4), ("shaderSource", 2), ("useProgram", 1), ("viewport", 4),
)


#: The pnames answered from what the context has bound: pname to bind target.
_BOUND_PARAMETERS = {
    34964: 34962,   # ARRAY_BUFFER_BINDING
    34965: 34963,   # ELEMENT_ARRAY_BUFFER_BINDING
    32873: 3553,    # TEXTURE_BINDING_2D
    34068: 34067,   # TEXTURE_BINDING_CUBE_MAP
    36006: 36160,   # FRAMEBUFFER_BINDING
    36007: 36161,   # RENDERBUFFER_BINDING
    35725: 35725,   # CURRENT_PROGRAM
}


#: The enums the code below compares against, named as the page names them.
_ENUMS = (
    ("INVALID_ENUM", 1280), ("NO_ERROR", 0), ("COMPILE_STATUS", 35713),
    ("LINK_STATUS", 35714), ("VALIDATE_STATUS", 35715), ("DELETE_STATUS", 35712),
    ("SHADER_TYPE", 35663), ("ACTIVE_ATTRIBUTES", 35721), ("ACTIVE_UNIFORMS", 35718),
    ("ATTACHED_SHADERS", 35717), ("BUFFER_SIZE", 34660), ("BUFFER_USAGE", 34661),
    ("FRAMEBUFFER_COMPLETE", 36053), ("FRAMEBUFFER_INCOMPLETE_MISSING_ATTACHMENT", 36055),
    ("COLOR_WRITEMASK", 3107),
)


#: The mask and formats ``clear`` and ``readPixels`` read.
_COLOR_BUFFER_BIT = 0x4000
_RGB = 0x1900
_FLOAT = 0x1406


def _typed(kind, values):
    return pyv8.JSTypeArray(list(values), kind)


def _clamped(value):
    """A clear colour channel the way ``clearColor`` stores it, as Chrome clamps it."""
    return min(max(float(value), 0.0), 1.0)


@interface('WebGLObject', None)
class WebGLObject:
    """What a context hands out for everything a page cannot query."""

    def __init__(self, context=None, **_):
        self._context = context


@interface('WebGLBuffer', 'WebGLObject')
class WebGLBuffer(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.size = 0
        self.usage = 35044


@interface('WebGLFramebuffer', 'WebGLObject')
class WebGLFramebuffer(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.attachments = {}


@interface('WebGLRenderbuffer', 'WebGLObject')
class WebGLRenderbuffer(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.width = 0
        self.height = 0
        self.has_storage = False


@interface('WebGLTexture', 'WebGLObject')
class WebGLTexture(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.has_image = False


@interface('WebGLShader', 'WebGLObject')
class WebGLShader(WebGLObject):
    def __init__(self, context=None, shader_type=35633, **_):
        super().__init__(context=context)
        self.shader_type = shader_type
        self.source = ""


@interface('WebGLProgram', 'WebGLObject')
class WebGLProgram(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.linked = False
        self.shaders = []


@interface('WebGLVertexArrayObject', 'WebGLObject')
class WebGLVertexArrayObject(WebGLObject):
    def __init__(self, context=None, **_):
        super().__init__(context=context)
        # Binding is what brings a vertex array into being, as ``isVertexArray`` reports.
        self.created = False


@interface('WebGLVertexArrayObjectOES', 'WebGLObject')
class WebGLVertexArrayObjectOES(WebGLObject):
    """What a WebGL 1 context hands out through ``OES_vertex_array_object``."""

    def __init__(self, context=None, **_):
        super().__init__(context=context)
        self.created = False


@interface('WebGLQuery', 'WebGLObject')
class WebGLQuery(WebGLObject):
    pass


@interface('WebGLTimerQueryEXT', 'WebGLObject')
class WebGLTimerQueryEXT(WebGLObject):
    """What a WebGL 1 context hands out through ``EXT_disjoint_timer_query``."""


@interface('WebGLSampler', 'WebGLObject')
class WebGLSampler(WebGLObject):
    pass


@interface('WebGLTransformFeedback', 'WebGLObject')
class WebGLTransformFeedback(WebGLObject):
    pass


@interface('WebGLSync', 'WebGLObject')
class WebGLSync(WebGLObject):
    pass


@interface('WebGLUniformLocation', None)
class WebGLUniformLocation:
    pass


@interface('WebGLActiveInfo', None)
class WebGLActiveInfo:
    def __init__(self, name="", size=0, value_type=0, **_):
        self._name, self._size, self._type = name, size, value_type

    def get_size(self): return self._size
    def get_type(self): return self._type
    def get_name(self): return self._name


@interface('WebGLShaderPrecisionFormat', None)
class WebGLShaderPrecisionFormat:
    def __init__(self, range_min=0, range_max=0, precision=0, **_):
        self._range = (range_min, range_max, precision)

    def get_rangeMin(self): return self._range[0]
    def get_rangeMax(self): return self._range[1]
    def get_precision(self): return self._range[2]


class _Extension:
    """What every extension object holds: the context that handed it out."""

    def __init__(self, context=None, **_):
        self._context = context


@interface('WebGLDebugRendererInfo', None)
class WebGLDebugRendererInfo(_Extension):
    """The extension whose two pnames name the machine's GL implementation."""


@interface('OESVertexArrayObject', None)
class OESVertexArrayObject(_Extension):
    def fn_createVertexArrayOES(self, *_):
        return self._context._remember(WebGLVertexArrayObjectOES(context=self._context))

    def fn_bindVertexArrayOES(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObjectOES):
            array.created = True

    def fn_deleteVertexArrayOES(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObjectOES):
            array.created = False

    def fn_isVertexArrayOES(self, array=None, *_):
        return isinstance(array, WebGLVertexArrayObjectOES) and array.created


@interface('EXTDisjointTimerQuery', None)
class EXTDisjointTimerQuery(_Extension):
    def fn_createQueryEXT(self, *_):
        return self._context._remember(WebGLTimerQueryEXT(context=self._context))

    def fn_isQueryEXT(self, query=None, *_):
        return isinstance(query, WebGLTimerQueryEXT)


@interface('WebGLLoseContext', None)
class WebGLLoseContext(_Extension):
    """The extension a page uses to lose and restore its context on purpose."""

    def fn_loseContext(self, *_):
        self._context._context_lost = True

    def fn_restoreContext(self, *_):
        # The reference browser never hands a lost context back.
        return None


def _shape_only_extension(interface_name):
    """An extension this module leaves to the baseline: constants and member shapes."""
    return interface(interface_name, None)(type(interface_name, (_Extension,), {
        "__doc__": f"The ``{interface_name}`` extension object.",
        "__module__": __name__,
    }))


def _extension_interfaces():
    """Every extension interface a context can hand out, by interface name."""
    composed = {cls.__idl_spec__.name: cls for cls in (
        WebGLDebugRendererInfo, OESVertexArrayObject, EXTDisjointTimerQuery, WebGLLoseContext,
    )}
    for _gl_name, interface_name in (*_WEBGL1_EXTENSIONS, *_WEBGL2_EXTENSIONS):
        if interface_name not in composed:
            composed[interface_name] = _shape_only_extension(interface_name)
    return composed


#: The extension objects a context hands out, by the interface behind each GL name.
EXTENSION_INTERFACES = _extension_interfaces()


#: What ``drawingBufferFormat`` answers on either context: ``RGBA8``.
_DRAWING_BUFFER_FORMAT = 32856


@interface('WebGLRenderingContext', None)
class WebGLRenderingContext:
        # Absent: ``fn_<member>`` binds them, and ``_VOID_MEMBERS``/``_STATE_MEMBERS`` are the bodies.

    _INTERFACE_NAME = "WebGLRenderingContext"

    #: The pnames this context answers with text, and the ones it answers with a value.
    _STRING_PARAMETERS = _STRINGS
    _SCALAR_PARAMETERS = _PARAMETERS

    def __init__(self, canvas=None, *_extra, **_):
        self._canvas = canvas
        self._error = 0
        self._enabled = set()
        self._objects = []
        self._bound = {}
        width = canvas.get_width() if canvas is not None else 300
        height = canvas.get_height() if canvas is not None else 150
        self._viewport = (0, 0, width, height)
        self._scissor = (0, 0, width, height)
        self._clear_color = (0.0, 0.0, 0.0, 0.0)
        self._color_mask = (True, True, True, True)
        self._cleared = False
        self._context_lost = False
        self._extension_objects = {}
        self._color_space = "srgb"
        self._unpack_color_space = "srgb"

    def _require(self, member, required, *values):
        provided = sum(1 for value in values if value is not MISSING)
        if provided < required:
            raise missing_error(self._INTERFACE_NAME, member, required, provided,
                                constructor=False)

    def _remember(self, value):
        self._objects.append(value)
        return value

    def get_canvas(self): return self._canvas
    def get_drawingBufferWidth(self): return self._canvas.get_width() if self._canvas else 300
    def get_drawingBufferHeight(self): return self._canvas.get_height() if self._canvas else 150
    def get_drawingBufferFormat(self): return _DRAWING_BUFFER_FORMAT
    def get_drawingBufferColorSpace(self): return self._color_space
    def set_drawingBufferColorSpace(self, value): self._color_space = str(dom_string(value))
    def get_unpackColorSpace(self): return self._unpack_color_space
    def set_unpackColorSpace(self, value): self._unpack_color_space = str(dom_string(value))

    # -- creation ---------------------------------------------------------------
    def fn_createBuffer(self, *_): return self._remember(WebGLBuffer(context=self))
    def fn_createFramebuffer(self, *_):
        return self._remember(WebGLFramebuffer(context=self))
    def fn_createProgram(self, *_): return self._remember(WebGLProgram(context=self))
    def fn_createRenderbuffer(self, *_):
        return self._remember(WebGLRenderbuffer(context=self))
    def fn_createTexture(self, *_): return self._remember(WebGLTexture(context=self))

    def fn_createShader(self, shader_type=MISSING, *arguments):
        self._require('createShader', 1, shader_type)
        return self._remember(WebGLShader(context=self, shader_type=shader_type))

    # -- state ------------------------------------------------------------------
    def fn_enable(self, cap=MISSING, *arguments):
        self._require('enable', 1, cap)
        self._enabled.add(cap)

    def fn_disable(self, cap=MISSING, *arguments):
        self._require('disable', 1, cap)
        self._enabled.discard(cap)

    def fn_isEnabled(self, cap=MISSING, *arguments):
        self._require('isEnabled', 1, cap)
        return cap in self._enabled

    def fn_viewport(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, *arguments):
        self._require('viewport', 4, x, y, width, height)
        self._viewport = (x, y, width, height)

    def fn_scissor(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, *arguments):
        self._require('scissor', 4, x, y, width, height)
        self._scissor = (x, y, width, height)

    def fn_clearColor(self, red=MISSING, green=MISSING, blue=MISSING, alpha=MISSING, *arguments):
        self._require('clearColor', 4, red, green, blue, alpha)
        self._clear_color = tuple(_clamped(value) for value in (red, green, blue, alpha))

    def fn_colorMask(self, red=MISSING, green=MISSING, blue=MISSING, alpha=MISSING, *arguments):
        self._require('colorMask', 4, red, green, blue, alpha)
        self._color_mask = tuple(bool(value) for value in (red, green, blue, alpha))

    def fn_clear(self, mask=MISSING, *arguments):
        self._require('clear', 1, mask)
        if int(mask) & _COLOR_BUFFER_BIT:
            self._cleared = True

    def fn_bindBuffer(self, target=MISSING, buffer=MISSING, *arguments):
        self._require('bindBuffer', 2, target, buffer)
        self._bound[target] = None if is_nullish(buffer) else buffer

    def fn_bindFramebuffer(self, target=MISSING, framebuffer=MISSING, *arguments):
        self._require('bindFramebuffer', 2, target, framebuffer)
        self._bound[36160] = None if is_nullish(framebuffer) else framebuffer

    def fn_bindRenderbuffer(self, target=MISSING, renderbuffer=MISSING, *arguments):
        self._require('bindRenderbuffer', 2, target, renderbuffer)
        self._bound[36161] = None if is_nullish(renderbuffer) else renderbuffer

    def fn_bindTexture(self, target=MISSING, texture=MISSING, *arguments):
        self._require('bindTexture', 2, target, texture)
        self._bound[target] = None if is_nullish(texture) else texture

    def fn_useProgram(self, program=MISSING, *arguments):
        self._require('useProgram', 1, program)
        self._bound[35725] = None if is_nullish(program) else program

    def fn_bufferData(self, target=MISSING, data=MISSING, usage=MISSING, *arguments):
        self._require('bufferData', 3, target, data, usage)
        buffer = self._bound.get(target)
        if isinstance(buffer, WebGLBuffer):
            buffer.size = int(getattr(data, "byteLength", 0) or 0)
            buffer.usage = usage

    def fn_framebufferTexture2D(self, target=MISSING, attachment=MISSING, textarget=MISSING,
                                texture=MISSING, level=MISSING, *arguments):
        self._require('framebufferTexture2D', 5, target, attachment, textarget, texture, level)
        framebuffer = self._bound.get(36160)
        if isinstance(framebuffer, WebGLFramebuffer):
            framebuffer.attachments[attachment] = texture

    def fn_framebufferRenderbuffer(self, target=MISSING, attachment=MISSING, rbtarget=MISSING,
                                   renderbuffer=MISSING, *arguments):
        self._require('framebufferRenderbuffer', 4, target, attachment, rbtarget, renderbuffer)
        framebuffer = self._bound.get(36160)
        if isinstance(framebuffer, WebGLFramebuffer):
            framebuffer.attachments[attachment] = renderbuffer

    def fn_texImage2D(self, target=MISSING, level=MISSING, internalformat=MISSING, *arguments):
        self._require('texImage2D', 6, target, level, internalformat, *arguments)
        texture = self._bound.get(target)
        if isinstance(texture, WebGLTexture):
            texture.has_image = True

    def fn_renderbufferStorage(self, target=MISSING, internalformat=MISSING, width=MISSING,
                               height=MISSING, *arguments):
        self._require('renderbufferStorage', 4, target, internalformat, width, height)
        renderbuffer = self._bound.get(36161)
        if isinstance(renderbuffer, WebGLRenderbuffer):
            renderbuffer.has_storage = True

    def fn_attachShader(self, program=MISSING, shader=MISSING, *arguments):
        self._require('attachShader', 2, program, shader)
        if isinstance(program, WebGLProgram) and shader not in program.shaders:
            program.shaders.append(shader)

    def fn_detachShader(self, program=MISSING, shader=MISSING, *arguments):
        self._require('detachShader', 2, program, shader)
        if isinstance(program, WebGLProgram) and shader in program.shaders:
            program.shaders.remove(shader)

    def fn_shaderSource(self, shader=MISSING, source=MISSING, *arguments):
        self._require('shaderSource', 2, shader, source)
        if isinstance(shader, WebGLShader):
            shader.source = str(dom_string(source))

    def fn_linkProgram(self, program=MISSING, *arguments):
        self._require('linkProgram', 1, program)
        if isinstance(program, WebGLProgram):
            stages = {shader.shader_type for shader in program.shaders
                      if isinstance(shader, WebGLShader)}
            # A program links only with both stages attached, which is how Chrome answers
            # for the vertex-only program a caller usually builds first.
            program.linked = self.VERTEX_SHADER in stages and self.FRAGMENT_SHADER in stages

    # -- queries ----------------------------------------------------------------
    def fn_getError(self, *_):
        error, self._error = self._error, 0
        return error

    def fn_isContextLost(self, *_): return self._context_lost

    def fn_getContextAttributes(self, *_):
        return {"alpha": True, "antialias": True, "depth": True, "desynchronized": False,
                "failIfMajorPerformanceCaveat": False, "powerPreference": "default",
                "premultipliedAlpha": True, "preserveDrawingBuffer": False, "stencil": False,
                "xrCompatible": False}

    def fn_getExtension(self, name=MISSING, *arguments):
        self._require('getExtension', 1, name)
        interface_name = self._extensions().get(str(dom_string(name)))
        if interface_name is None:
            return pyv8.Null
        return self._extension_object(interface_name)

    def _extensions(self):
        """The GL name to interface map this context answers for."""
        return _WEBGL1_EXTENSION_MAP

    def _extension_object(self, interface_name):
        """The one object this context hands back for an extension, built on first ask."""
        existing = self._extension_objects.get(interface_name)
        if existing is not None:
            return existing
        # This interface is not a global, so nothing materialises it: a Python-built
        # instance has to be committed to the realm before it can become a JS object.
        # The guard is the one ``document.all`` uses: materialising a template that is
        # already installed is refused, and a second canvas asks for it again.
        services = getattr(self._canvas, "_services", None)
        realm = getattr(services, "realm_services", None)
        if realm is not None and not realm.interface_materialized(interface_name):
            realm.materialize_interface(interface_name)
        value = EXTENSION_INTERFACES[interface_name](context=self)
        self._extension_objects[interface_name] = value
        return value

    def fn_getSupportedExtensions(self, *_): return list(self._extensions())

    def fn_getParameter(self, pname=MISSING, *arguments):
        self._require('getParameter', 1, pname)
        target = _BOUND_PARAMETERS.get(pname)
        if target is not None:
            bound = self._bound.get(target)
            return pyv8.Null if bound is None else bound
        if pname == self.VIEWPORT:
            return _typed(pyv8.ArrayType.Int32Array, self._viewport)
        if pname == self.SCISSOR_BOX:
            return _typed(pyv8.ArrayType.Int32Array, self._scissor)
        if pname == self.COLOR_CLEAR_VALUE:
            return _typed(pyv8.ArrayType.Float32Array, self._clear_color)
        if pname == self.COLOR_WRITEMASK:
            return list(self._color_mask)
        if pname in (_UNMASKED_VENDOR_WEBGL, _UNMASKED_RENDERER_WEBGL):
            return self._unmasked(pname)
        for _name, constant, kind, values in _ARRAY_PARAMETERS:
            if pname == constant and values is not None:
                return _typed(kind, values)
        for _name, constant, text in self._STRING_PARAMETERS:
            if pname == constant:
                return text
        for _name, constant, value in self._SCALAR_PARAMETERS:
            if pname == constant:
                return pyv8.Null if value is None else value
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def _unmasked(self, pname):
        """The GL identity the profile carries, for the two debug renderer pnames."""
        environment = getattr(getattr(self._canvas, "_services", None), "environment", None)
        if environment is None:
            return ""
        settings = environment.webgl
        return settings.vendor if pname == _UNMASKED_VENDOR_WEBGL else settings.renderer

    def fn_getShaderPrecisionFormat(self, shader_type=MISSING, precision_type=MISSING,
                                    *arguments):
        self._require('getShaderPrecisionFormat', 2, shader_type, precision_type)
        if precision_type in (self.LOW_FLOAT, self.MEDIUM_FLOAT, self.HIGH_FLOAT):
            return WebGLShaderPrecisionFormat(127, 127, 23)
        if precision_type in (self.LOW_INT, self.MEDIUM_INT, self.HIGH_INT):
            return WebGLShaderPrecisionFormat(31, 30, 0)
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getShaderParameter(self, shader=MISSING, pname=MISSING, *arguments):
        self._require('getShaderParameter', 2, shader, pname)
        if not isinstance(shader, WebGLShader):
            self._error = 1282
            return pyv8.Null
        if pname == self.COMPILE_STATUS:
            return True
        if pname == self.DELETE_STATUS:
            return False
        if pname == self.SHADER_TYPE:
            return shader.shader_type
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getShaderSource(self, shader=MISSING, *arguments):
        self._require('getShaderSource', 1, shader)
        return shader.source if isinstance(shader, WebGLShader) else pyv8.Null

    def fn_getShaderInfoLog(self, shader=MISSING, *arguments):
        self._require('getShaderInfoLog', 1, shader)
        return ""

    def fn_getProgramParameter(self, program=MISSING, pname=MISSING, *arguments):
        self._require('getProgramParameter', 2, program, pname)
        if not isinstance(program, WebGLProgram):
            self._error = 1282
            return pyv8.Null
        if pname in (self.LINK_STATUS, self.VALIDATE_STATUS):
            return program.linked
        if pname == self.DELETE_STATUS:
            return False
        if pname in (self.ACTIVE_ATTRIBUTES, self.ACTIVE_UNIFORMS):
            return 0
        if pname == self.ATTACHED_SHADERS:
            return list(program.shaders)
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getProgramInfoLog(self, program=MISSING, *arguments):
        self._require('getProgramInfoLog', 1, program)
        return ""

    def fn_getAttachedShaders(self, program=MISSING, *arguments):
        self._require('getAttachedShaders', 1, program)
        return list(program.shaders) if isinstance(program, WebGLProgram) else pyv8.Null

    def fn_getAttribLocation(self, program=MISSING, name=MISSING, *arguments):
        self._require('getAttribLocation', 2, program, name)
        return -1

    def fn_getUniformLocation(self, program=MISSING, name=MISSING, *arguments):
        self._require('getUniformLocation', 2, program, name)
        return pyv8.Null

    def fn_getUniform(self, program=MISSING, location=MISSING, *arguments):
        self._require('getUniform', 2, program, location)
        return pyv8.Null

    def fn_getActiveAttrib(self, program=MISSING, index=MISSING, *arguments):
        self._require('getActiveAttrib', 2, program, index)
        return pyv8.Null

    def fn_getActiveUniform(self, program=MISSING, index=MISSING, *arguments):
        self._require('getActiveUniform', 2, program, index)
        return pyv8.Null

    def fn_checkFramebufferStatus(self, target=MISSING, *arguments):
        self._require('checkFramebufferStatus', 1, target)
        framebuffer = self._bound.get(36160)
        if isinstance(framebuffer, WebGLFramebuffer):
            attached = list(framebuffer.attachments.values())
            # An attachment without an image or storage is not an attachment to Chrome.
            if attached and all(getattr(item, "has_image", False)
                                or getattr(item, "has_storage", False) for item in attached):
                return self.FRAMEBUFFER_COMPLETE
        return self.FRAMEBUFFER_INCOMPLETE_MISSING_ATTACHMENT

    def fn_getBufferParameter(self, target=MISSING, pname=MISSING, *arguments):
        self._require('getBufferParameter', 2, target, pname)
        buffer = self._bound.get(target)
        if not isinstance(buffer, WebGLBuffer):
            self._error = 1282
            return pyv8.Null
        if pname == self.BUFFER_SIZE:
            return buffer.size
        if pname == self.BUFFER_USAGE:
            return buffer.usage
        self._error = self.INVALID_ENUM
        return pyv8.Null

    def fn_getVertexAttribOffset(self, index=MISSING, pname=MISSING, *arguments):
        self._require('getVertexAttribOffset', 2, index, pname)
        return 0

    def fn_getVertexAttrib(self, index=MISSING, pname=MISSING, *arguments):
        self._require('getVertexAttrib', 2, index, pname)
        return 0

    def fn_getTexParameter(self, target=MISSING, pname=MISSING, *arguments):
        self._require('getTexParameter', 2, target, pname)
        return pyv8.Null

    def fn_getFramebufferAttachmentParameter(self, target=MISSING, attachment=MISSING,
                                             pname=MISSING, *arguments):
        self._require('getFramebufferAttachmentParameter', 3, target, attachment, pname)
        return pyv8.Null

    def fn_getRenderbufferParameter(self, target=MISSING, pname=MISSING, *arguments):
        self._require('getRenderbufferParameter', 2, target, pname)
        return 0

    def fn_isBuffer(self, value=None, *_):
        return isinstance(value, WebGLBuffer) and value in self._objects

    def fn_isFramebuffer(self, value=None, *_):
        return isinstance(value, WebGLFramebuffer) and value in self._objects

    def fn_isProgram(self, value=None, *_):
        return isinstance(value, WebGLProgram) and value in self._objects

    def fn_isRenderbuffer(self, value=None, *_):
        return isinstance(value, WebGLRenderbuffer) and value in self._objects

    def fn_isShader(self, value=None, *_):
        return isinstance(value, WebGLShader) and value in self._objects

    def fn_isTexture(self, value=None, *_):
        return isinstance(value, WebGLTexture) and value in self._objects

    def fn_readPixels(self, x=MISSING, y=MISSING, width=MISSING, height=MISSING, format=MISSING,
                      type=MISSING, pixels=MISSING, *arguments):
        self._require('readPixels', 7, x, y, width, height, format, type, pixels)
        view = pixels if isinstance(pixels, pyv8.JSTypeArray) else None
        if view is None:
            self._error = self.INVALID_ENUM
            return None
        # Nothing draws yet, so the buffer holds the clear colour, or stays as it was.
        components = 3 if format == _RGB else 4
        values = self._pixel_values(type, components) if self._cleared else [0] * components
        for index in range(min(int(view._get("length")), int(width) * int(height) * components)):
            view[index] = values[index % components]
        return None

    def _pixel_values(self, type, components):
        """The clear colour as ``readPixels`` hands it back, honouring ``colorMask``."""
        values = [value if self._color_mask[index] else 0.0
                  for index, value in enumerate(self._clear_color[:components])]
        return values if type == _FLOAT else [int(round(value * 255)) for value in values]


def _void_member(name, required):
    def call(self, *arguments):
        if len(arguments) < required:
            raise missing_error('WebGLRenderingContext', name, required, len(arguments),
                                constructor=False)
        return None
    return call


@interface('WebGL2RenderingContext', 'WebGLRenderingContext')
class WebGL2RenderingContext(WebGLRenderingContext):
    """What a ``webgl2`` canvas hands out: the WebGL 1 answers, plus WebGL 2's."""

    _INTERFACE_NAME = "WebGL2RenderingContext"
    _STRING_PARAMETERS = _WEBGL2_STRINGS
    # The WebGL 2 entries come first: the ones whose limit moved outrank WebGL 1's.
    _SCALAR_PARAMETERS = _WEBGL2_PARAMETERS + _PARAMETERS

    def _extensions(self):
        return _WEBGL2_EXTENSION_MAP

    def fn_createVertexArray(self, *_):
        return self._remember(WebGLVertexArrayObject(context=self))

    def fn_bindVertexArray(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObject):
            array.created = True

    def fn_deleteVertexArray(self, array=None, *_):
        if isinstance(array, WebGLVertexArrayObject):
            array.created = False

    def fn_isVertexArray(self, value=None, *_):
        return isinstance(value, WebGLVertexArrayObject) and value.created

    def fn_createQuery(self, *_):
        return self._remember(WebGLQuery(context=self))

    def fn_isQuery(self, value=None, *_):
        return isinstance(value, WebGLQuery) and value in self._objects

    def fn_createSampler(self, *_):
        return self._remember(WebGLSampler(context=self))

    def fn_isSampler(self, value=None, *_):
        return isinstance(value, WebGLSampler) and value in self._objects

    def fn_createTransformFeedback(self, *_):
        return self._remember(WebGLTransformFeedback(context=self))

    def fn_isTransformFeedback(self, value=None, *_):
        return isinstance(value, WebGLTransformFeedback) and value in self._objects

    def fn_fenceSync(self, condition=MISSING, flags=MISSING, *arguments):
        self._require('fenceSync', 2, condition, flags)
        return self._remember(WebGLSync(context=self))

    def fn_isSync(self, value=None, *_):
        return isinstance(value, WebGLSync) and value in self._objects

for _name, _required in _VOID_MEMBERS:
    setattr(WebGLRenderingContext, "fn_" + _name, _void_member(_name, _required))
for _name, _constant, _value in _PARAMETERS:
    setattr(WebGLRenderingContext, _name, _constant)
for _name, _constant, _kind, _values in _ARRAY_PARAMETERS:
    setattr(WebGLRenderingContext, _name, _constant)
for _name, _constant, _text in _STRINGS:
    setattr(WebGLRenderingContext, _name, _constant)
for _name, _value in _ENUMS:
    setattr(WebGLRenderingContext, _name, _value)
for _name, _value in (
    ("VERTEX_SHADER", 35633), ("FRAGMENT_SHADER", 35632), ("LOW_FLOAT", 36336),
    ("MEDIUM_FLOAT", 36337), ("HIGH_FLOAT", 36338), ("LOW_INT", 36339), ("MEDIUM_INT", 36340),
    ("HIGH_INT", 36341),
):
    setattr(WebGLRenderingContext, _name, _value)

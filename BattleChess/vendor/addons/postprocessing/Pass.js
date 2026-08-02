import {
	OrthographicCamera,
	BufferGeometry,
	Float32BufferAttribute,
	Mesh
} from 'three';

/**
 * Abstract base class for all post processing passes.
 *
 * This module is normally shipped as part of the three.js addons bundle. It was
 * missing from `vendor/addons/postprocessing/` while every other pass in that
 * directory imports it (`EffectComposer`, `RenderPass`, `ShaderPass`,
 * `MaskPass`, `OutputPass` and `UnrealBloomPass` all do
 * `import { Pass } from './Pass.js'`), so the composer chain required by
 * CONTRACT.md section 9 could not be constructed. Restored here verbatim to the
 * r180 interface those files are written against.
 *
 * @abstract
 * @three_import import { Pass } from 'three/addons/postprocessing/Pass.js';
 */
class Pass {

	/**
	 * Constructs a new pass.
	 */
	constructor() {

		/**
		 * This flag can be used for type testing.
		 *
		 * @type {boolean}
		 * @readonly
		 * @default true
		 */
		this.isPass = true;

		/**
		 * If set to `false`, the pass is skipped by the composer.
		 *
		 * @type {boolean}
		 * @default true
		 */
		this.enabled = true;

		/**
		 * Whether the composer should swap its read and write buffer
		 * after this pass has been executed.
		 *
		 * @type {boolean}
		 * @default true
		 */
		this.needsSwap = true;

		/**
		 * Whether the pass should clear its render target before rendering.
		 *
		 * @type {boolean}
		 * @default false
		 */
		this.clear = false;

		/**
		 * Whether the pass renders into the default framebuffer (the screen)
		 * or into its write buffer. Managed by {@link EffectComposer}, which
		 * sets it on the last enabled pass of the chain.
		 *
		 * @type {boolean}
		 * @default false
		 */
		this.renderToScreen = false;

	}

	/**
	 * Sets the size of the pass. Overwrite this method in derived classes
	 * that hold size-dependent resources (render targets, resolution uniforms).
	 *
	 * @param {number} width - The width in physical pixels.
	 * @param {number} height - The height in physical pixels.
	 */
	setSize( /* width, height */ ) {}

	/**
	 * Executes the pass. Must be implemented by derived classes.
	 *
	 * @abstract
	 * @param {WebGLRenderer} renderer - The renderer.
	 * @param {WebGLRenderTarget} writeBuffer - The write buffer.
	 * @param {WebGLRenderTarget} readBuffer - The read buffer, holding the
	 * result of the previous pass.
	 * @param {number} deltaTime - The delta time in seconds.
	 * @param {boolean} maskActive - Whether masking is active or not.
	 */
	render( /* renderer, writeBuffer, readBuffer, deltaTime, maskActive */ ) {

		console.error( 'THREE.Pass: .render() must be implemented in derived pass.' );

	}

	/**
	 * Frees the GPU-related resources allocated by this instance. Overwrite this
	 * method in derived classes that allocate render targets, materials or
	 * full-screen quads.
	 */
	dispose() {}

}

// --- helper for passes that draw a single full-screen primitive ------------

const _camera = new OrthographicCamera( - 1, 1, 1, - 1, 0, 1 );

/**
 * A single oversized triangle covering the viewport. Cheaper than a quad — one
 * primitive, no diagonal seam — and the UVs are set so the visible [0,1] range
 * still maps exactly across the screen.
 *
 * @private
 * @augments BufferGeometry
 */
class FullscreenTriangleGeometry extends BufferGeometry {

	constructor() {

		super();

		this.setAttribute( 'position', new Float32BufferAttribute( [ - 1, 3, 0, - 1, - 1, 0, 3, - 1, 0 ], 3 ) );
		this.setAttribute( 'uv', new Float32BufferAttribute( [ 0, 2, 0, 0, 2, 0 ], 2 ) );

	}

}

const _geometry = new FullscreenTriangleGeometry();

/**
 * Utility class for rendering a single material across the whole viewport.
 * Used by every pass that post-processes the read buffer.
 *
 * @three_import import { FullScreenQuad } from 'three/addons/postprocessing/Pass.js';
 */
class FullScreenQuad {

	/**
	 * Constructs a new full screen quad.
	 *
	 * @param {?Material} [material=null] - The material to render.
	 */
	constructor( material ) {

		this._mesh = new Mesh( _geometry, material );

	}

	/**
	 * Frees the GPU-related resources allocated by this instance.
	 */
	dispose() {

		this._mesh.geometry.dispose();

	}

	/**
	 * Renders the full screen quad.
	 *
	 * @param {WebGLRenderer} renderer - The renderer.
	 */
	render( renderer ) {

		renderer.render( this._mesh, _camera );

	}

	/**
	 * The quad's material.
	 *
	 * @type {?Material}
	 */
	get material() {

		return this._mesh.material;

	}

	set material( value ) {

		this._mesh.material = value;

	}

}

export { Pass, FullScreenQuad };
